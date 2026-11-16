"""Security scan steps written in plain words (Gherkin), read without a model.

A step such as::

    When I run a security scan for "cve, exposure, misconfig" failing on "high"
    When I run a security scan of the OpenAPI spec "specs/pets.yaml" for "sqli, xss"
    When I scan the app for security issues on the path "/api"

becomes a step with a ``security`` payload (:class:`~testzeus_hercules.cases.models.SecurityScan`).
Recognised parts: tags in quotes after ``for`` or ``tags`` (or tag words such as ``cve``,
``xss``, ``sql injection``), ``the OpenAPI spec "<file>"``, ``failing on "<severity>"``,
``on the path "<path>"``, ``at 10 requests per second``. A ``Then`` right after it that says there are no findings
(``Then there should be no security findings at or above "medium"``) becomes the step's
``expected`` outcome (and its severity the step's ``fail_on``), so the scan's gate checks it.
Intrusive templates are never turned on from words: set ``intrusive: true`` in the case.

v1 security features (one scenario per topic, such as ``When I check for publicly
disclosed vulnerabilities``) are read by :func:`v1_security_steps`, used by ``hercules
import v1``: the topic words choose the tags, and the base URL step is dropped.
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import ValidationError

from testzeus_hercules.cases.models import SecurityScan, Step
from testzeus_hercules.cases.security import INTRUSIVE_TAGS

__all__ = ["fold_scan_steps", "is_security_scenario", "scan_from_text", "v1_security_steps"]

_VERIFY = "verify"
_SCAN_SENTENCE = re.compile(
    r"^(?:(?:i|we)\s+)?(?:(?:run|perform|do|start|execute)s?\s+(?:a\s+|an\s+)?(?:security|vulnerability|nuclei)\s+scan\b"
    r"|scans?\s+(?:the\s+)?(?:app|application|site|website|web\s*site|api|service)\b(?:\s+for\s+(?:security\s+issues|vulnerabilities|security\s+findings))?)",
    re.IGNORECASE,
)
_QUOTED_TAGS = re.compile(r"\b(?:for|tags?|with\s+tags?|templates?)\s+[\"“']([^\"”']+)[\"”']", re.IGNORECASE)
_OPENAPI = re.compile(r"\b(?:openapi|swagger)(?:\s+spec(?:ification)?|\s+file|\s+document)?\s+[\"“']([^\"”']+)[\"”']", re.IGNORECASE)
_FAIL_ON = re.compile(r"\bfail(?:s|ing)?\s+(?:on|at)\s+[\"“']?(critical|high|medium|low|info)[\"”']?", re.IGNORECASE)
_RATE = re.compile(r"\b(?:at|with)\s+(?:most\s+)?(\d{1,4})\s+requests?\s+(?:per|a|each)\s+second\b", re.IGNORECASE)
_PATH = re.compile(r"\b(?:on|at)\s+(?:the\s+)?path\s+[\"“']([^\"”']+)[\"”']", re.IGNORECASE)
_NO_FINDINGS = re.compile(r"\b(?:no|not\s+(?:be\s+)?any|zero|without(?:\s+any)?)\s+(?:security\s+|scan\s+)?(?:findings?|vulnerabilit(?:y|ies)|issues?|cves?|problems?)\b", re.IGNORECASE)
_SEVERITY_WORD = re.compile(r"\b(?:at\s+or\s+above|of\s+at\s+least|severity)\s+[\"“']?(critical|high|medium|low|info)[\"”']?", re.IGNORECASE)
_BASE_URL_STEP = re.compile(r"^(?:(?:i|we)\s+)?(?:have|has|am\s+given|use|open)\s+(?:a\s+|the\s+)?base[_\s-]?url\b", re.IGNORECASE)
_SECURITY_NAME = re.compile(r"\b(?:security|sec[\s_-]?testing|pen[\s_-]?test(?:ing)?|vulnerabilit(?:y|ies))\b", re.IGNORECASE)
_TAG = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

# Words of a step that name a template tag (v1's security topics and common names).
_TOPICS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), tag)
    for pattern, tag in (
        (r"\bcves?\b|publicly\s+disclosed\s+vulnerabilit", "cve"),
        (r"\badmin(?:istration)?\s+panels?\b|\bpanels?\b|\bdashboards?\b", "panel"),
        (r"\bwordpress\b|\bplugins?\s+and\s+themes\b", "wordpress"),
        (r"\bexposures?\b|\bexposed\b|sensitive\s+(?:data|information)", "exposure"),
        (r"\bxss\b|cross[\s-]site\s+scripting", "xss"),
        (r"\bosint\b|public\s+sources|publicly\s+available\s+information", "osint"),
        (r"\btech(?:nology)?\s+stack\b|\bthe\s+stack\b|\btech\b", "tech"),
        (r"\bmisconfig(?:uration|ured)?s?\b", "misconfig"),
        (r"\blfi\b|local\s+file(?:s|\s+inclusion)?|include\s+local\s+files", "lfi"),
        (r"\brce\b|remote\s+(?:code\s+)?execution", "rce"),
        (r"\bexploit[\s-]?db\b|\bedb\b", "edb"),
        (r"\bpacket\s?storm\b", "packetstorm"),
        (r"\bdevops\b|\bpipelines?\b", "devops"),
        (r"\bsqli\b|sql\s+(?:injection|payloads?)", "sqli"),
        (r"\bcloud\b", "cloud"),
        (r"\bunauth(?:enticated|orized)?\b|without\s+(?:valid\s+)?credentials", "unauth"),
        (r"\bauthenticated\b|with\s+valid\s+credentials", "authenticated"),
        (r"\bssrf\b|server[\s-]side\s+request\s+forgery", "ssrf"),
        (r"\bopen\s+redirects?\b", "redirect"),
        (r"\b(?:sub)?domain\s+takeovers?\b|\btakeovers?\b", "takeover"),
    )
)


def _topic_tags(text: str) -> list[str]:
    return [tag for pattern, tag in _TOPICS if pattern.search(text)]


def _tags(text: str) -> list[str]:
    quoted = _QUOTED_TAGS.search(text)
    if quoted is not None:
        tags = [part.strip().lower() for part in re.split(r"[,;]|\band\b", quoted.group(1)) if part.strip()]
        return [tag for tag in dict.fromkeys(tags) if _TAG.match(tag)]
    return list(dict.fromkeys(_topic_tags(text)))


def scan_from_text(text: str) -> SecurityScan | None:
    """The scan a step's words ask for, or None when they are not a security scan step (or name an invalid one)."""
    if not _SCAN_SENTENCE.match(text.strip()):
        return None
    fields: dict[str, object] = {"tags": [tag for tag in _tags(text) if tag not in INTRUSIVE_TAGS]}
    for pattern, name in ((_OPENAPI, "openapi"), (_PATH, "path")):
        found = pattern.search(text)
        if found is not None:
            fields[name] = found.group(1).strip()
    fail_on = _FAIL_ON.search(text)
    if fail_on is not None:
        fields["fail_on"] = fail_on.group(1).lower()
    rate = _RATE.search(text)
    if rate is not None:
        fields["rate_limit"] = int(rate.group(1))
    try:
        return SecurityScan.model_validate(fields)
    except ValidationError:
        return None


def _says_no_findings(text: str) -> bool:
    return bool(_NO_FINDINGS.search(text))


def _with_expected(step: Step, scan: SecurityScan, expected: str, *, explicit_fail_on: bool) -> Step:
    severity = _SEVERITY_WORD.search(expected) or _FAIL_ON.search(expected)
    if severity is not None and not explicit_fail_on:
        scan = scan.model_copy(update={"fail_on": severity.group(1).lower()})
    joined = expected if step.expected is None else f"{step.expected}; {expected}"
    return step.model_copy(update={"security": scan, "expected": joined[:4000]})


def fold_scan_steps(steps: Iterable[Step]) -> list[Step]:
    """``steps`` with each plain-words scan step given its ``security`` payload, and the ``Then`` steps right after it
    that say there are no findings folded into its expected outcome."""
    result: list[Step] = []
    open_scan: tuple[int, bool] | None = None  # (position in result, fail_on written in the step)
    for step in steps:
        if step.security is None and step.api is None and step.sql is None and step.quality is None and step.action.strip().lower() != _VERIFY:
            scan = scan_from_text(step.action)
            if scan is not None:
                result.append(step.model_copy(update={"security": scan}))
                open_scan = (len(result) - 1, _FAIL_ON.search(step.action) is not None)
                continue
        if open_scan is not None and step.action.strip().lower() == _VERIFY and step.expected and _says_no_findings(step.expected):
            position, explicit = open_scan
            scanned = result[position]
            assert scanned.security is not None
            result[position] = _with_expected(scanned, scanned.security, step.expected, explicit_fail_on=explicit)
            continue
        open_scan = None
        result.append(step)
    return result


def is_security_scenario(names: Iterable[str | None], tags: Iterable[str]) -> bool:
    """True when a v1 feature or scenario is a security test (by its name or tags)."""
    return any(_SECURITY_NAME.search(name or "") for name in names) or any(_SECURITY_NAME.search(tag) or tag.lower() in ("sec", "security") for tag in tags)


def v1_security_steps(steps: Iterable[Step], context: str = "") -> list[Step] | None:
    """A v1 security scenario's steps as scan steps: each ``When`` that names a topic (or, when none does, the first
    ``When`` of a scenario whose name ``context`` names one) becomes a scan for its tags, the ``Then`` after it its
    expected outcome; the base URL step is dropped. None when nothing names a topic, or only intrusive testing does
    (intrusive templates are never turned on by an import)."""
    steps = list(steps)
    named = any(_topic_tags(step.action) for step in steps if step.action.strip().lower() != _VERIFY)
    fallback = [] if named else [tag for tag in _topic_tags(context) if tag not in INTRUSIVE_TAGS]
    converted: list[Step] = []
    scanned = False
    for step in steps:
        check_only = step.action.strip().lower() == _VERIFY
        if not check_only and _BASE_URL_STEP.match(step.action.strip()):
            continue
        if check_only and converted and converted[-1].security is not None and step.expected:
            converted[-1] = converted[-1].model_copy(update={"expected": step.expected if converted[-1].expected is None else f"{converted[-1].expected}; {step.expected}"})
            continue
        if not check_only and step.security is None:
            explicit = scan_from_text(step.action)
            tags = [tag for tag in _topic_tags(step.action) if tag not in INTRUSIVE_TAGS] or ([] if scanned else fallback)
            if explicit is not None or tags:
                scanned = True
                converted.append(step.model_copy(update={"security": explicit or SecurityScan(tags=tags)}))
                continue
        converted.append(step)
    return converted if scanned else None
