import json
import re
from typing import Any

from testzeus_hercules.utils.logger import logger

# Bounds for the balanced-JSON scan in parse_response: without them a message
# full of unclosed "{" characters (an 8k-brace LLM glitch costs ~2.3s in the
# planner path) makes every candidate rescan forward to the end of the message,
# which is quadratic in the message length. The budget caps the TOTAL characters
# scanned across all candidates and the cap limits how many candidates are
# inspected; tripping either simply stops collecting, which falls through to the
# existing best-effort / empty-dict paths (mirrors
# intelligence/prd_parser._iter_balanced_segments).
_JSON_SCAN_CHAR_BUDGET = 1_000_000
_JSON_SCAN_MAX_CANDIDATES = 64

# Keys that mark a JSON object as a planner decision.
_PLANNER_KEYS = ("plan", "next_step", "terminate", "final_response", "target_helper")

# Plain-text terminal answers emitted by navigation helpers.
TERMINATE_TASK_MARKER = "##TERMINATE TASK##"
# A verdict prefix needs a separator (colon, dash, en dash or em dash) right
# after PASS/FAIL, so a legend line like "PASS/FAIL legend: which is which" is
# not read as terminal.
_TERMINAL_STATUS_PATTERN = re.compile(r"^\s*(PASS|FAIL)\s*[:\-–—]", re.IGNORECASE)
_TERMINAL_FAILURE_MARKERS = ("[error]", "[tool error]", "incomplete", "uncertain", "max nav rounds")
# Canonical helper verdict sections (see e.g. the browser nav agent's response
# format): "### Success:" or "### Error or Uncertainty:" followed by the
# section body, up to the next "###" section header or the end of the message.
_CANONICAL_SECTION_HEADERS = ("Success", "Error or Uncertainty")


def _canonical_section_content(text: str, header: str) -> str:
    """Return the non-empty trimmed content under a ``### <header>:`` section, or ""."""
    for match in re.finditer(rf"### {re.escape(header)}:[ \t]*", text, re.IGNORECASE):
        rest = text[match.end() :]
        # A terminate marker bounds the section: narration after it is not
        # part of the section body (it cannot flip a Success verdict).
        marker_index = rest.find(TERMINATE_TASK_MARKER)
        body = rest if marker_index == -1 else rest[:marker_index]
        next_section = re.search(r"\n###", body)
        if next_section:
            body = body[: next_section.start()]
        content = body.replace(TERMINATE_TASK_MARKER, "").strip()
        if content:
            return content
    return ""


def _parse_terminal_text(message: str) -> dict[str, Any]:
    """Normalize a plain-text terminal helper answer into planner-shaped fields.

    Navigation helpers finish a task with an explicit ``PASS:``/``FAIL:``
    verdict, the canonical ``### Success:``/``### Error or Uncertainty:``
    sections and/or the terminate marker. Without normalization those
    plain-text answers are invisible to the JSON parser and the final test
    outcome is lost. Messages without any terminal signal return an empty dict.
    """
    stripped = message.strip()
    status_match = _TERMINAL_STATUS_PATTERN.match(stripped)
    success_content = _canonical_section_content(stripped, "Success")
    error_content = _canonical_section_content(stripped, "Error or Uncertainty")
    has_terminate_marker = TERMINATE_TASK_MARKER in stripped
    if status_match is None and not has_terminate_marker and not (success_content or error_content):
        return {}
    final_response = stripped.replace(TERMINATE_TASK_MARKER, "").strip() or stripped
    terminal_response: dict[str, Any] = {"terminate": "yes", "final_response": final_response}
    whole_message_failure_marker = any(m in final_response.lower() for m in _TERMINAL_FAILURE_MARKERS)
    if success_content or error_content:
        # Canonical verdict sections: an Error-or-Uncertainty section wins over
        # a Success one (conservative). A Success verdict consults the
        # failure-marker list only within the Success section's own content —
        # markers in unrelated narration elsewhere in the message must not
        # flip it.
        success_failure_marker = any(m in success_content.lower() for m in _TERMINAL_FAILURE_MARKERS)
        is_passed = bool(success_content) and not error_content and not success_failure_marker
        terminal_response.update({"is_assert": True, "is_passed": is_passed, "assert_summary": final_response})
        return terminal_response
    status = status_match.group(1).upper() if status_match else ""
    if status or whole_message_failure_marker:
        is_passed = status == "PASS" and not whole_message_failure_marker
        terminal_response.update({"is_assert": True, "is_passed": is_passed, "assert_summary": final_response})
    return terminal_response


def _scan_json_candidates(message: str) -> list[str]:
    """Find balanced top-level JSON objects, string-aware and bounded.

    The scan is string-aware (braces inside JSON strings do not affect depth)
    and bounded by _JSON_SCAN_CHAR_BUDGET / _JSON_SCAN_MAX_CANDIDATES so
    pathological malformed output fails fast instead of scanning quadratically.
    """
    json_candidates: list[str] = []
    scanned = 0
    for m in re.finditer(r"\{", message):
        if len(json_candidates) >= _JSON_SCAN_MAX_CANDIDATES or scanned >= _JSON_SCAN_CHAR_BUDGET:
            break
        start = m.start()
        depth = 0
        in_string = False
        escaped = False
        for i, ch in enumerate(message[start:], start):
            scanned += 1
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    json_candidates.append(message[start : i + 1])
                    break
    return json_candidates


def _last_planner_object(message: str) -> dict[str, Any] | None:
    """Return the last complete JSON object that looks like a planner response.

    Trying the message intact (before any newline rewriting) keeps escaped
    sequences such as ``\\n`` inside string values meaningful; rewriting them
    first corrupts the escape and drops fields (e.g. target_helper) into the
    salvage path. Returns None when nothing parses intact.
    """
    for candidate in reversed(_scan_json_candidates(message)):
        try:
            parsed_candidate = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed_candidate, dict) and any(key in parsed_candidate for key in _PLANNER_KEYS):
            return parsed_candidate
    return None


def parse_response(message: str) -> dict[str, Any]:
    """
    Parse the response from the browser agent and return the response as a dictionary.
    """
    if not message or not str(message).strip():
        return {}

    # Check if message is wrapped in ```json ``` blocks
    if "```json" in message:
        start_idx = message.find("```json") + 7
        end_idx = message.find("```", start_idx + 7)
        message = message[start_idx:end_idx]
    else:
        # Original handling for ``` blocks
        if message.startswith("```"):
            message = message[3:]
        if message.endswith("```"):
            message = message[:-3]
        if message.startswith("json"):
            message = message[4:]

    message = message.strip()

    json_response: dict[str, Any] = {}

    # Intact-first attempt: the repair pass below rewrites escape sequences, so
    # a valid response containing e.g. "\\n" inside string values would be
    # corrupted and lose fields. Only attempted when a backslash is present —
    # without one the rewrite is a no-op for escapes and the repaired scan below
    # sees the same bytes.
    if "\\" in message:
        intact_json = _last_planner_object(message)
        if intact_json is not None:
            return intact_json

    message = message.replace("\\n", "\n")
    message = message.replace("\n", " ")  # type: ignore

    # Find all top-level JSON objects and take the last one (most recent planner response).
    json_candidates = _scan_json_candidates(message)
    if json_candidates:
        chosen: str | None = None
        for candidate in reversed(json_candidates):
            try:
                parsed_candidate = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed_candidate, dict) and any(key in parsed_candidate for key in _PLANNER_KEYS):
                chosen = candidate
                break
        message = chosen or json_candidates[-1]  # take the last complete JSON object
    elif not any(key in message for key in _PLANNER_KEYS):
        # No JSON anywhere: a plain-text terminal answer (PASS/FAIL verdict,
        # canonical Success/Error sections or the terminate marker) still
        # carries the final outcome.
        terminal_response = _parse_terminal_text(message)
        if terminal_response:
            return terminal_response
        logger.debug("Skipping non-JSON response parsing for message: %s", message[:200])
        return {}

    try:
        json_response: dict[str, Any] = json.loads(message)
    except Exception as e:
        logger.warning(
            "LLM response was not properly formed JSON. Will try to use it as is. " 'LLM response: "%s". Error: %s',
            message[:200],
            e,
        )

        if "plan" in message and "next_step" in message:
            start = message.index("plan") + len("plan")
            end = message.index("next_step")
            json_response["plan"] = message[start:end].replace('"', "").strip()
        if "next_step" in message and "terminate" in message:
            start = message.index("next_step") + len("next_step")
            end = message.index("terminate")
            json_response["next_step"] = message[start:end].replace('"', "").strip()
        if "terminate" in message and "final_response" in message:
            start = message.index("terminate") + len("terminate")
            end = message.index("final_response")
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

            start = message.index("final_response") + len("final_response")
            end = len(message) - 1
            json_response["final_response"] = message[start:end].replace('"', "").strip()

        elif "terminate" in message:
            start = message.index("terminate") + len("terminate")
            end = len(message) - 1
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

    return json_response
