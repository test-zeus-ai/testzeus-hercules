"""Fakes for the execution-engine tests: snapshots, elements and a DOM-backed perceiver.

``FakeElement`` and ``FakeSnapshot`` mirror the shape of the perception model so the
engine can be tested before (and independently of) the real perceive package.
``DomPerceiver`` builds snapshots from a live Playwright page with a few lines of
JavaScript; it is deliberately simple and only good enough for the fixture pages.
"""

from __future__ import annotations

import dataclasses

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class FakeBBox:
    x: float = 0.0
    y: float = 0.0
    width: float = 100.0
    height: float = 20.0


@dataclass(frozen=True)
class FakeElement:
    md: int
    role: str
    name: str
    tag: str = ""
    attrs: Mapping[str, str] = field(default_factory=dict)
    bbox: FakeBBox | None = None
    frame: str | None = None
    in_viewport: bool = True
    tier: int = 0
    value: str | None = None
    checked: bool | None = None
    disabled: bool = False


@dataclass(frozen=True)
class FakeSnapshot:
    seq: int
    url: str = "https://app.test/"
    title: str = "Fixture"
    viewport: tuple[int, int] = (1280, 800)
    elements: tuple[FakeElement, ...] = ()
    plain_jpeg: bytes = b"\xff\xd8plain"
    som_jpeg: bytes = b"\xff\xd8som"
    above: tuple[str, ...] = ()
    below: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    headings: tuple[tuple[str, float], ...] = ()

    def element(self, md: int) -> FakeElement:
        for element in self.elements:
            if element.md == md:
                return element
        raise KeyError(md)

    def observation_text(self, max_elements: int = 120, max_tokens: int = 2500) -> str:
        lines = [f"# page: {self.title} · {self.url} · snapshot {self.seq}"]
        for element in self.elements[:max_elements]:
            value = f' | val="{element.value}"' if element.value is not None else ""
            lines.append(f'[{element.md}] {element.role} "{element.name}"{value}')
        return "\n".join(lines)


def login_elements() -> tuple[FakeElement, ...]:
    return (
        FakeElement(md=1, role="heading", name="Sign in to Acme", tag="h1"),
        FakeElement(md=2, role="textbox", name="Username", tag="input", attrs={"id": "username", "name": "username", "type": "text"}),
        FakeElement(md=3, role="textbox", name="Password", tag="input", attrs={"id": "password", "name": "password", "type": "password"}),
        FakeElement(md=4, role="link", name="Forgot password?", tag="a", attrs={"href": "/forgot"}),
        FakeElement(md=5, role="checkbox", name="Remember me", tag="input", attrs={"type": "checkbox"}, checked=False),
        FakeElement(md=6, role="button", name="Sign in", tag="button", attrs={"type": "submit"}),
        FakeElement(md=7, role="combobox", name="Language", tag="select", value="English"),
    )


_COLLECT_JS = """
() => {
  const roleOf = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'submit' || type === 'button') return 'button';
      return 'textbox';
    }
    return 'generic';
  };
  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    if (el.id) {
      const label = document.querySelector(`label[for="${el.id}"]`);
      if (label) return label.innerText.trim();
    }
    if (el.tagName.toLowerCase() === 'input') return el.getAttribute('placeholder') || el.value || '';
    return (el.innerText || '').trim();
  };
  const nodes = document.querySelectorAll('a, button, input, select, textarea, h1, h2, h3, [role]');
  const out = [];
  let md = 0;
  for (const el of nodes) {
    md += 1;
    el.setAttribute('data-test-md', String(md));
    const rect = el.getBoundingClientRect();
    const attrs = {};
    for (const key of ['id', 'name', 'type', 'placeholder', 'href']) {
      const value = el.getAttribute(key);
      if (value) attrs[key] = value;
    }
    out.push({
      md, role: roleOf(el), name: nameOf(el), tag: el.tagName.toLowerCase(), attrs,
      in_viewport: rect.bottom > 0 && rect.top < window.innerHeight,
      value: ('value' in el && el.type !== 'password') ? String(el.value) : null,
      checked: ('checked' in el && (el.type === 'checkbox' || el.type === 'radio')) ? el.checked : null,
      disabled: !!el.disabled,
    });
  }
  return out;
}
"""


class DomPerceiver:
    """A tiny DOM-walking perceiver for fixture pages (marks elements with ``data-test-md``)."""

    def __init__(self) -> None:
        self.snapshots: list[FakeSnapshot] = []

    async def snapshot(self, page: Any, seq: int, focus_terms: Sequence[str] = ()) -> FakeSnapshot:
        rows = await page.evaluate(_COLLECT_JS)
        elements = tuple(
            FakeElement(
                md=row["md"],
                role=row["role"],
                name=row["name"],
                tag=row["tag"],
                attrs=row["attrs"],
                in_viewport=row["in_viewport"],
                value=row["value"],
                checked=row["checked"],
                disabled=row["disabled"],
            )
            for row in rows
        )
        jpeg = await page.screenshot(type="jpeg", quality=60)
        snap = FakeSnapshot(seq=seq, url=page.url, title=await page.title(), elements=elements, plain_jpeg=jpeg, som_jpeg=jpeg)
        self.snapshots.append(snap)
        return snap

    async def resolve(self, page: Any, snapshot: Any, md: int) -> Any:
        return page.locator(f'[data-test-md="{md}"]')


# --------------------------------------------------------------------------- engine fakes


@dataclass(frozen=True)
class FakeLocator:
    md: int


@dataclass
class FakePage:
    url: str = "about:blank"


@dataclass(frozen=True)
class FakeResult:
    ok: bool
    detail: str
    url_after: str = ""
    kind: str | None = None
    retry: bool = True
    chosen: dict[str, Any] | None = None


class FakeActions:
    """Records calls; page text, URL changes and per-element failures are scripted."""

    def __init__(
        self,
        *,
        text: str = "",
        fail_mds: Sequence[int] = (),
        invisible_mds: Sequence[int] = (),
        url_after_click: str | None = None,
        text_after_click: str | None = None,
        disabled_mds: Sequence[int] = (),
        mismatch_mds: Sequence[int] = (),
        options: Mapping[int, Sequence[str]] | None = None,
    ) -> None:
        self.text = text
        self.fail_mds = set(fail_mds)
        self.disabled_mds = set(disabled_mds)
        self.mismatch_mds = set(mismatch_mds)
        self.options = dict(options or {})
        self.invisible_mds = set(invisible_mds)
        self.url_after_click = url_after_click
        self.text_after_click = text_after_click
        self.download_content = b"Order ID,Status\n1001,Paid\n1002,Paid\n"
        self.calls: list[tuple[Any, ...]] = []

    def _ok(self, page: Any, detail: str) -> FakeResult:
        return FakeResult(ok=True, detail=detail, url_after=getattr(page, "url", ""))

    async def open_url(self, page: Any, url: str, *, base_url: str | None = None, timeout_ms: int = 0, file_roots: Any = None) -> FakeResult:
        self.calls.append(("open_url", url, base_url))
        if url.startswith("javascript:"):
            return FakeResult(ok=False, detail="cannot open a 'javascript' URL")
        page.url = url if "://" in url else (base_url or "") + url
        return self._ok(page, f"opened {page.url}")

    async def _element(self, name: str, locator: FakeLocator, *extra: Any) -> FakeResult:
        self.calls.append((name, locator.md, *extra))
        if locator.md in self.fail_mds:
            return FakeResult(ok=False, detail=f"{name} failed: the element is disabled")
        if locator.md in self.disabled_mds:
            return FakeResult(ok=False, detail=f"{name} failed: the element is disabled", kind="disabled")
        if name == "fill" and locator.md in self.mismatch_mds:
            return FakeResult(ok=False, detail="fill failed: the field holds '61201-02-20' after filling, not the requested value", kind="value_mismatch", retry=False)
        return FakeResult(ok=True, detail=name)

    async def click(self, locator: FakeLocator, *, timeout_ms: int = 0) -> FakeResult:
        result = await self._element("click", locator)
        if result.ok and self.text_after_click is not None:
            self.text = self.text_after_click
        return result

    async def fill(self, locator: FakeLocator, value: str, *, timeout_ms: int = 0) -> FakeResult:
        return await self._element("fill", locator, value)

    async def select(self, locator: FakeLocator, value: str, *, timeout_ms: int = 0) -> FakeResult:
        return await self._element("select", locator, value)

    async def download_by_click(self, locator: FakeLocator, folder: Any, *, expected_name: str | None = None, timeout_ms: int = 0) -> FakeResult:
        result = await self._element("download", locator, expected_name)
        if not result.ok:
            return result
        return self._downloaded(folder, expected_name or "report.csv")

    async def download_from_url(self, page: Any, url: str, folder: Any, *, base_url: str | None = None, expected_name: str | None = None, **_: Any) -> FakeResult:
        self.calls.append(("download_from_url", url, expected_name))
        return self._downloaded(folder, expected_name or url.rsplit("/", 1)[-1])

    def _downloaded(self, folder: Any, name: str) -> FakeResult:
        from pathlib import Path

        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.download_content)
        return FakeResult(ok=True, detail=f"downloaded '{name}'", chosen={"file": str(path), "name": name, "size": len(self.download_content), "type": "csv", "via": "download"})

    async def upload(self, locator: FakeLocator, names: list[str], *, roots: Any = (), timeout_ms: int = 0) -> FakeResult:
        return await self._element("upload", locator, tuple(names))

    async def check(self, locator: FakeLocator, *, timeout_ms: int = 0) -> FakeResult:
        return await self._element("check", locator)

    async def uncheck(self, locator: FakeLocator, *, timeout_ms: int = 0) -> FakeResult:
        return await self._element("uncheck", locator)

    async def hover(self, locator: FakeLocator, *, timeout_ms: int = 0) -> FakeResult:
        return await self._element("hover", locator)

    async def press(self, target: Any, key: str, *, timeout_ms: int = 0) -> FakeResult:
        self.calls.append(("press", getattr(target, "md", None), key))
        return FakeResult(ok=True, detail=f"pressed {key}")

    async def wait_seconds(self, page: Any, seconds: float) -> FakeResult:
        self.calls.append(("wait_seconds", seconds))
        return FakeResult(ok=True, detail="waited")

    async def wait_for_text(self, page: Any, text: str, *, timeout_ms: int = 0) -> FakeResult:
        self.calls.append(("wait_for_text", text))
        found = " ".join(text.split()).casefold() in " ".join(self.text.split()).casefold()
        return FakeResult(ok=found, detail="shown" if found else f"text {text!r} did not appear")

    async def page_text(self, page: Any) -> str:
        return self.text

    async def read_value(self, locator: FakeLocator, *, timeout_ms: int = 0) -> str | None:
        return None

    async def select_options(self, locator: FakeLocator, *, timeout_ms: int = 0) -> list[str] | None:
        return list(self.options[locator.md]) if locator.md in self.options else None

    async def is_visible(self, locator: FakeLocator, *, timeout_ms: int = 0) -> bool:
        return locator.md not in self.invisible_mds

    async def text_visible(self, page: Any, text: str, *, timeout_ms: int = 0) -> bool:
        return text.casefold() in self.text.casefold()

    async def scroll(self, page: Any, *, pixels: int) -> FakeResult:
        self.calls.append(("scroll", pixels))
        return FakeResult(ok=True, detail="scrolled")

    async def screenshot_jpeg(self, page: Any) -> bytes | None:
        return b"\xff\xd8shot"


class FakePerceiver:
    """Returns scripted snapshots in order (repeating the last); resolves md to a FakeLocator."""

    def __init__(self, *snapshots: FakeSnapshot, gone: Sequence[int] = (), error: Exception | None = None) -> None:
        self._snapshots = list(snapshots) or [FakeSnapshot(seq=0, elements=login_elements())]
        self.gone = set(gone)
        self.error = error
        self.calls: list[tuple[int, tuple[str, ...]]] = []

    async def snapshot(self, page: Any, seq: int, focus_terms: Sequence[str] = ()) -> FakeSnapshot:
        if self.error is not None:
            raise self.error
        self.calls.append((seq, tuple(focus_terms)))
        base = self._snapshots[min(len(self.calls) - 1, len(self._snapshots) - 1)]
        return dataclasses.replace(base, seq=seq)

    async def resolve(self, page: Any, snapshot: Any, md: int) -> FakeLocator:
        from testzeus_hercules.engine.ports import TargetGone

        if md in self.gone:
            raise TargetGone(f"element {md} is gone")
        return FakeLocator(md)


# --------------------------------------------------------------------------- model fakes (step reading and decisions)


def intent(
    kind: str, target: str | None = None, *, value: str | None = None, url: str | None = None, expected: str | None = None, role: str | None = None, match: str | None = None, then: Sequence[Any] = ()
) -> Any:
    """A StepIntent as a model would read it (``then``: the step's further actions)."""
    from testzeus_hercules.engine.intent import StepIntent

    return StepIntent.model_validate({"kind": kind, "target": target, "value": value, "url": url, "expected": expected, "role": role, "match": match, "then": tuple(then)})


def opens(url: str) -> Any:
    return intent("navigate", url=url)


def clicks(target: str, role: str | None = None) -> Any:
    return intent("click", target, role=role)


def fills(target: str, value: str) -> Any:
    return intent("fill", target, value=value)


def sees(text: str) -> Any:
    return intent("assert_text", expected=text)


class ScriptedInterpreter:
    """Test double for the step reader: answers with the StepIntents a test scripted for each step text.

    ``readings`` maps a step text (an action, or an expected outcome) to one intent or a
    list of intents (the step's actions in order); unscripted texts read as ``unknown``.
    ``remaining`` scripts the answers to continuation/re-reading questions in order
    (None: the model cannot answer). ``case_reading`` False makes the whole-case reading
    come back empty, so every step is read on its own.
    """

    def __init__(
        self,
        readings: Mapping[str, Any] | None = None,
        *,
        remaining: Sequence[Any] = (),
        model: Any = None,
        case_reading: bool = True,
        needs: Mapping[str, Sequence[int]] | None = None,
        field_answers: Sequence[Any] = (),
        first_answers: Sequence[Any] = (),
        extras: Mapping[str, Mapping[str, Any]] | None = None,
        repairs: Mapping[str, Any] | None = None,
    ) -> None:
        from testzeus_hercules.engine.intent import normalise_step_text

        self._normalise = normalise_step_text
        # Per step text: the case reading's criteria, risk and lint for it ({"criteria": [...], "risk": {...}, "lint": [...]}).
        self.extras = {normalise_step_text(text): value for text, value in (extras or {}).items()}
        # Per step text: how a repair reads it (a coverage gap read again); and the gaps each repair call was asked about.
        self.repairs = {normalise_step_text(text): self._joined(value) for text, value in (repairs or {}).items()}
        self.repair_calls: list[dict[int, str]] = []
        self.readings = {normalise_step_text(text): self._joined(value) for text, value in (readings or {}).items()}
        self._remaining = list(remaining)
        self._model = model
        self.case_reading = case_reading
        self.case_calls: list[list[Any]] = []
        self.calls: list[tuple[str, str | None]] = []
        self.remaining_calls: list[tuple[str, tuple[str, ...], str | None]] = []
        self.remaining_context: list[dict[str, tuple[str, ...]]] = []
        # Per step text: the steps its field needs first; scripted answers to field_value and run_first, in order.
        self.needs = {normalise_step_text(text): tuple(steps) for text, steps in (needs or {}).items()}
        self.field_answers = list(field_answers)
        self.first_answers = list(first_answers)
        self.field_calls: list[tuple[str, Any, tuple[str, ...], tuple[str, ...]]] = []
        self.first_calls: list[tuple[str, str, tuple[tuple[int, str], ...]]] = []

    @staticmethod
    def _joined(value: Any) -> Any:
        parts = list(value) if isinstance(value, (list, tuple)) else [value]
        first, *rest = parts
        return first.model_copy(update={"then": tuple(rest)})

    @property
    def model(self) -> Any:
        return self._model

    def _lookup(self, text: str, source: str) -> Any:
        found = self.readings.get(self._normalise(text))
        if found is None:
            return intent("unknown", self._normalise(text)).model_copy(update={"source": source})
        return found.model_copy(update={"source": source, "then": tuple(part.model_copy(update={"source": source}) for part in found.then)})

    async def read_case(self, items: Sequence[Any], *, names: Sequence[str] = ()) -> Any:
        from testzeus_hercules.engine.intent import CaseReading

        self.case_calls.append(list(items))
        if not self.case_reading:
            return CaseReading()
        intents = {(item.step, item.role): (item.text, self._lookup(item.text, "case")) for item in items if self._normalise(item.text) in self.readings}
        needs = {item.step: self.needs[self._normalise(item.text)] for item in items if item.role == "action" and self._normalise(item.text) in self.needs}
        extras = {item.step: dict(self.extras[self._normalise(item.text)]) for item in items if item.role == "action" and self._normalise(item.text) in self.extras}
        return CaseReading(intents=intents, source="model", needs=needs, extras=extras)

    async def repair_case(self, items: Sequence[Any], gaps: Mapping[int, str], *, names: Sequence[str] = ()) -> Any:
        """Read the gap steps again: the scripted ``repairs`` readings (per step text) replace the first readings."""
        from testzeus_hercules.engine.intent import CaseReading

        self.repair_calls.append(dict(gaps))
        intents = {}
        for item in items:
            text = self._normalise(item.text)
            if item.step in gaps and text in self.repairs:
                intents[(item.step, item.role)] = (item.text, self.repairs[text].model_copy(update={"source": "case"}))
        return CaseReading(intents=intents, source="model")

    async def field_value(self, text: str, field: Any, *, later: Sequence[str] = (), rejected: Sequence[str] = ()) -> Any:
        self.field_calls.append((text, field, tuple(later), tuple(rejected)))
        return self.field_answers.pop(0) if self.field_answers else None

    async def run_first(self, text: str, problem: str, later: Sequence[tuple[int, str]]) -> Any:
        self.first_calls.append((text, problem, tuple(later)))
        return self.first_answers.pop(0) if self.first_answers else None

    async def interpret(self, text: str, *, fingerprint: str = "", context: Any = None) -> Any:
        page = await context() if context is not None else None
        self.calls.append((text, page))
        return self._lookup(text, "page" if page is not None else "case")

    async def remaining(
        self, text: str, done: Sequence[str], *, fingerprint: str = "", context: Any = None, reading: Sequence[str] = (), earlier: Sequence[str] = (), expected: Sequence[str] = ()
    ) -> Any:
        page = await context() if context is not None else None
        self.remaining_calls.append((text, tuple(done), page))
        self.remaining_context.append({"reading": tuple(reading), "earlier": tuple(earlier), **({"expected": tuple(expected)} if expected else {})})
        if not self._remaining:
            return ()
        answer = self._remaining.pop(0)
        if answer is None:
            return None
        parts = tuple(answer) if isinstance(answer, (list, tuple)) else (answer,)
        return tuple(part.model_copy(update={"source": "page"}) for part in parts)


def _labels(row: Mapping[str, Any]) -> set[str]:
    attrs = row.get("attrs") or {}
    names = [row.get("name"), attrs.get("label"), attrs.get("aria-label"), attrs.get("placeholder"), attrs.get("title"), attrs.get("alt")]
    return {" ".join(str(name).split()).casefold() for name in names if name}


class LabelDecider:
    """Test double for a decision model: picks the row whose label equals the step's target, exactly.

    Every other question gets ``judge`` (yes/no questions: semantic, visual and precondition
    judgements) or the stub's default answer. Records every request.
    """

    engine = "scripted"

    def __init__(self, *, judge: float = 0.9, confidence: float = 0.95, category: str | None = None, tool: str | None = None, tool_confidence: float = 0.9) -> None:
        self.judge = judge
        self.confidence = confidence
        self.category = category
        self.tool = tool
        self.tool_confidence = tool_confidence
        self.calls: list[tuple[Any, str]] = []

    async def decide(self, request: Any, *, purpose: str) -> Any:
        from testzeus_hercules.decide.backends.stub import default_answer
        from testzeus_hercules.decide.contract import ChoiceAnswer, Decision, NoulAnswer, NoulQuestion

        self.calls.append((request, purpose))
        step = request.state.get("step") or {}
        wanted = " ".join(str(step.get("target") or "").split()).casefold()
        rows = [row for row in request.state.get("rows") or () if wanted and wanted in _labels(row)]
        role = step.get("role")
        if role and any(row.get("role") == role for row in rows):
            rows = [row for row in rows if row.get("role") == role]
        answers: dict[str, Any] = {}
        for qid, question in request.questions.items():
            if qid == "target":
                choice = rows[0]["id"] if rows else "no_match"
                confidence = self.confidence if rows else 0.9
                rest = (1 - confidence) / max(1, len(question.criteria) - 1)
                answers[qid] = ChoiceAnswer(choice=choice, confidence=confidence, probabilities={name: confidence if name == choice else rest for name in question.criteria})
            elif qid == "target_absent":
                answers[qid] = NoulAnswer(noul=0.05 if rows else 0.9)
            elif qid == "tool" and self.tool is not None:
                weight = self.tool_confidence
                answers[qid] = ChoiceAnswer(
                    choice=self.tool,
                    confidence=weight,
                    probabilities={name: weight if name == self.tool else (1 - weight) / max(1, len(question.criteria) - 1) * (weight > 0) for name in question.criteria},
                )
            elif qid == "category" and self.category is not None:
                answers[qid] = ChoiceAnswer(choice=self.category, confidence=0.9, probabilities={name: 0.9 if name == self.category else 0.1 / 3 for name in question.criteria})
            elif isinstance(question, NoulQuestion):
                answers[qid] = NoulAnswer(noul=self.judge)
            else:
                answers[qid] = default_answer(question)
        return Decision(answers=answers, engine=self.engine, model_version="scripted", latency_ms=0, cost_usd=0.0).validate_against(request)
