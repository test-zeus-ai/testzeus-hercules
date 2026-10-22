"""Offline tests for PRD text extraction, chunking and normalized PRD building."""

import asyncio
import json
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest
from testzeus_hercules.intelligence import prd_parser
from testzeus_hercules.utils.url_guard import FetchResult
from testzeus_hercules.intelligence.normalized_prd import (
    NormalizedPRD,
    PRDRequirement,
    prd_from_json,
    prd_to_json,
)
from testzeus_hercules.intelligence.prd_parser import (
    build_normalized_prd,
    chunk_text,
    extract_text,
    parse_json_array,
    parse_json_object,
    strip_html_tags,
)


class FakeLLM:
    """Offline LLM double: pops scripted responses in order, repeating the last one."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._last = responses[-1] if responses else ""
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        content = self._responses.pop(0) if self._responses else self._last
        return SimpleNamespace(content=content)


def _prd_text(paragraphs: int = 2) -> str:
    """A PRD-like document whose body paragraphs each exceed ~470 characters."""
    body = []
    for index in range(1, paragraphs + 1):
        body.append(f"Section {index}: The system shall provide feature number {index}. " + "detail " * 60)
    return "# Auth Suite Requirements\n\n" + "\n\n".join(body)


def _requirements_json(*titles: str) -> str:
    return json.dumps([{"title": title, "description": f"Description of {title}", "acceptance_criteria": [f"{title} works"]} for title in titles])


# --- extract_text -----------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_extract_text_reads_text_files(tmp_path, suffix: str) -> None:
    source = tmp_path / f"prd{suffix}"
    source.write_text("# Title\n\nBody text with details.", encoding="utf-8")

    metadata: dict = {}
    text = extract_text(str(source), metadata)

    assert "Body text with details." in text
    assert metadata["source_type"] == "file"
    assert metadata["file_type"] == suffix.lstrip(".")
    assert metadata["source"] == str(source)


def test_extract_text_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_text(str(tmp_path / "does_not_exist.md"))


def test_extract_text_url_strips_html(monkeypatch) -> None:
    html = "<html><head><style>body{color:red}</style></head><body><h1>Hello</h1><p>World &amp; more</p></body></html>"
    seen: dict = {}

    def fake_fetch(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return FetchResult(url=url, status_code=200, text=html)

    monkeypatch.setattr(prd_parser, "fetch_text", fake_fetch)

    metadata: dict = {}
    text = extract_text("https://example.com/prd", metadata)

    assert "Hello" in text
    assert "World & more" in text
    assert "<" not in text
    assert metadata["source_type"] == "url"
    assert metadata["url"] == "https://example.com/prd"
    assert seen["kwargs"]["allow_private"] is False


def test_extract_text_url_private_hosts_opt_in(monkeypatch) -> None:
    seen: dict = {}

    def fake_fetch(url, **kwargs):
        seen.update(kwargs)
        return FetchResult(url=url, status_code=200, text="ok")

    monkeypatch.setattr(prd_parser, "fetch_text", fake_fetch)
    monkeypatch.setenv("PRD_FETCH_ALLOW_PRIVATE_HOSTS", "true")
    extract_text("http://wiki.internal/prd", {})
    assert seen["allow_private"] is True


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/prd", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/x", "http://[::1]/x"])
def test_extract_text_url_blocks_internal_targets_by_default(url) -> None:
    with pytest.raises(ValueError, match="Refusing to fetch"):
        extract_text(url, {})


def test_extract_text_docx_falls_back_to_zipfile(tmp_path, monkeypatch) -> None:
    def _unavailable(path: str) -> str:
        raise ImportError("unstructured is not available")

    monkeypatch.setattr(prd_parser, "_docx_text_unstructured", _unavailable)

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Hello Document</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    source = tmp_path / "prd.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    metadata: dict = {}
    text = extract_text(str(source), metadata)

    assert "Hello Document" in text
    assert "Second paragraph" in text
    assert metadata["file_type"] == "docx"


def test_extract_text_corrupt_pdf_raises_value_error(tmp_path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"this is definitely not a pdf document")

    with pytest.raises(ValueError, match="could not parse PDF"):
        extract_text(str(source))


def test_extract_text_corrupt_docx_raises_value_error(tmp_path, monkeypatch) -> None:
    def _unavailable(path: str) -> str:
        raise ImportError("unstructured is not available")

    monkeypatch.setattr(prd_parser, "_docx_text_unstructured", _unavailable)
    source = tmp_path / "broken.docx"
    source.write_text("not a zip archive", encoding="utf-8")

    with pytest.raises(ValueError, match="could not parse DOCX"):
        extract_text(str(source))


# --- chunk_text -------------------------------------------------------------------


def test_chunk_text_small_text_is_single_chunk() -> None:
    assert chunk_text("Para one.\n\nPara two.") == ["Para one.\n\nPara two."]
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_chunk_text_respects_max_chars_and_keeps_paragraphs_whole() -> None:
    paragraphs = [f"Paragraph {index}: " + "x" * 120 for index in range(10)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, max_chars=500, overlap=80)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 500
    joined = "\n".join(chunks)
    for paragraph in paragraphs:
        assert paragraph in joined


def test_chunk_text_overlap_carries_tail_into_next_chunk() -> None:
    paragraphs = [f"Paragraph {index}: " + "x" * 120 for index in range(10)]
    text = "\n\n".join(paragraphs)

    chunks = chunk_text(text, max_chars=500, overlap=80)

    for previous, following in zip(chunks, chunks[1:]):
        tail = following.split("\n\n")[0]
        assert tail
        assert previous.endswith(tail)


def test_chunk_text_invalid_max_chars_raises() -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", max_chars=0)


def test_chunk_text_rejects_non_integer_limits() -> None:
    """Non-integer max_chars/overlap raise the documented ValueError, not TypeError (fuzz finding 4)."""
    with pytest.raises(ValueError, match="max_chars must be an integer"):
        chunk_text("some text", max_chars=float("nan"))
    with pytest.raises(ValueError, match="max_chars must be an integer"):
        chunk_text("some text", max_chars=1.5)
    with pytest.raises(ValueError, match="overlap must be an integer"):
        chunk_text("a\n\nb", max_chars=10, overlap=0.5)


def test_chunk_text_hard_split_line_keeps_seam_overlap() -> None:
    """A long single line is hard-split with built-in overlap (F2).

    Boundary text must survive: a sentence placed away from a seam stays intact
    in one chunk, and text spanning the hard-split seam appears in more than
    one chunk (previously the pieces were exactly max_chars, so no overlap was
    possible exactly when a requirement straddled the seam).
    """
    max_chars, overlap = 200, 50
    prefix = "filler " * 8
    sentence = "The user shall be able to reset a forgotten password via email."
    line = prefix + sentence + " " + "detail " * 60  # single long line -> hard-split path

    chunks = chunk_text(line, max_chars=max_chars, overlap=overlap)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= max_chars
    assert sentence in "\n\n".join(chunks)
    step = max_chars - overlap
    seam = line[step:max_chars]
    assert seam
    assert sum(1 for chunk in chunks if seam in chunk) >= 2


# --- build_normalized_prd ----------------------------------------------------------


def test_build_normalized_prd_merges_and_renumbers(tmp_path) -> None:
    source = tmp_path / "prd.md"
    source.write_text(_prd_text(paragraphs=2), encoding="utf-8")

    llm = FakeLLM(
        [
            _requirements_json("User Login"),
            json.dumps(
                [
                    {"title": "user login", "description": "Duplicate of the first requirement."},
                    {"title": "Password Reset", "description": "Users can reset passwords.", "acceptance_criteria": ["reset email sent"]},
                ]
            ),
            json.dumps({"project_name": "Auth Suite", "summary": "A suite of authentication features."}),
        ]
    )

    prd = asyncio.run(build_normalized_prd(str(source), llm=llm, max_chars_per_chunk=500))

    assert prd.source_metadata["chunk_count"] == 2
    assert prd.source_metadata["failed_chunks"] == 0
    assert [requirement.id for requirement in prd.requirements] == ["REQ-001", "REQ-002"]
    assert [requirement.title for requirement in prd.requirements] == ["User Login", "Password Reset"]
    assert prd.requirements[0].acceptance_criteria == ["User Login works"]
    assert prd.project_name == "Auth Suite"
    assert prd.summary == "A suite of authentication features."
    assert len(llm.prompts) == 3
    assert "project_name" in llm.prompts[2]  # third call is the summary pass


def test_build_normalized_prd_summary_falls_back_to_heading(tmp_path) -> None:
    source = tmp_path / "nimbus.md"
    source.write_text("# Nimbus Notes Requirements\n\nUsers can create notes.\n\nUsers can share notes.", encoding="utf-8")

    llm = FakeLLM([_requirements_json("Create Note")])

    prd = asyncio.run(build_normalized_prd(str(source), llm=llm))

    # The summary pass receives the JSON array, fails to parse an object, and falls back.
    assert prd.project_name == "Nimbus Notes Requirements"
    assert prd.summary == ""
    assert [requirement.id for requirement in prd.requirements] == ["REQ-001"]


def test_build_normalized_prd_skips_garbage_chunk(tmp_path) -> None:
    source = tmp_path / "prd.md"
    source.write_text(_prd_text(paragraphs=2), encoding="utf-8")

    llm = FakeLLM(
        [
            "I am sorry, I cannot produce JSON for this excerpt.",
            _requirements_json("Export Report"),
            json.dumps({"project_name": "Reports", "summary": "Reporting features."}),
        ]
    )

    prd = asyncio.run(build_normalized_prd(str(source), llm=llm, max_chars_per_chunk=500))

    assert [requirement.id for requirement in prd.requirements] == ["REQ-001"]
    assert prd.requirements[0].title == "Export Report"
    assert prd.source_metadata["chunk_count"] == 2
    assert prd.source_metadata["failed_chunks"] == 1
    assert prd.source_metadata["requirement_count"] == 1


def test_build_normalized_prd_all_chunks_failed_raises_with_chunk_count(tmp_path) -> None:
    source = tmp_path / "prd.md"
    source.write_text(_prd_text(paragraphs=2), encoding="utf-8")

    llm = FakeLLM(["no valid json [<bad>] here"])

    with pytest.raises(ValueError, match=r"2 of 2 chunk extractions failed"):
        asyncio.run(build_normalized_prd(str(source), llm=llm, max_chars_per_chunk=500))


def test_build_normalized_prd_accepts_string_acceptance_criteria(tmp_path) -> None:
    source = tmp_path / "prd.md"
    source.write_text("# Reports PRD\n\nUsers can export reports.\n\nUsers can schedule reports.", encoding="utf-8")

    llm = FakeLLM(
        [
            json.dumps(
                [
                    {"title": "Export Report", "acceptance_criteria": "- file downloads\n- format is CSV"},
                    {"title": "Schedule Report", "acceptance_criteria": "runs daily"},
                ]
            ),
            json.dumps({"project_name": "Reports", "summary": "Reporting features."}),
        ]
    )

    prd = asyncio.run(build_normalized_prd(str(source), llm=llm))

    assert prd.requirements[0].acceptance_criteria == ["file downloads", "format is CSV"]
    assert prd.requirements[1].acceptance_criteria == ["runs daily"]


def test_build_normalized_prd_extracts_text_off_event_loop(tmp_path, monkeypatch) -> None:
    """extract_text runs in a worker thread so it cannot block the event loop (F12)."""
    source = tmp_path / "prd.md"
    source.write_text("# Off Loop\n\nUsers can export reports.", encoding="utf-8")

    real_extract = prd_parser.extract_text
    idents: list[int] = []

    def spy_extract(source_arg: str, metadata: dict | None = None) -> str:
        idents.append(threading.get_ident())
        return real_extract(source_arg, metadata)

    monkeypatch.setattr(prd_parser, "extract_text", spy_extract)

    async def _loop_ident() -> int:
        return threading.get_ident()

    loop_ident = asyncio.run(_loop_ident())
    llm = FakeLLM([_requirements_json("Export Report")])
    asyncio.run(build_normalized_prd(str(source), llm=llm))

    assert len(idents) == 1
    assert idents[0] != loop_ident


def test_build_normalized_prd_all_garbage_raises(tmp_path) -> None:
    source = tmp_path / "prd.md"
    source.write_text("# Empty PRD\n\nNothing useful here.", encoding="utf-8")

    llm = FakeLLM(["<not json at all>"])

    with pytest.raises(ValueError, match="No requirements could be extracted"):
        asyncio.run(build_normalized_prd(str(source), llm=llm))


# --- fuzzy requirement dedupe + source_metadata version -------------------------------


def test_dedupe_requirements_fuzzy_merges_reworded_titles() -> None:
    """The fuzzy pass merges near-identical titles from chunk overlap; genuinely different ones survive (backlog P3)."""
    requirements = [
        PRDRequirement(id="TEMP-001", title="User login with email and password", description="first", acceptance_criteria=["c1"]),
        PRDRequirement(id="TEMP-002", title="User login with email & password", description="second", acceptance_criteria=["c2"]),
        PRDRequirement(id="TEMP-003", title="Login", description="login only", acceptance_criteria=["c3"]),
        PRDRequirement(id="TEMP-004", title="Logout", description="logout only", acceptance_criteria=["c4"]),
    ]

    deduped = prd_parser._dedupe_requirements(requirements)

    assert [requirement.title for requirement in deduped] == ["User login with email and password", "Login", "Logout"]
    merged = deduped[0]
    assert merged.id == "TEMP-001"  # the FIRST requirement's id/title/description are kept
    assert merged.description == "first"
    assert merged.acceptance_criteria == ["c1", "c2"]  # criteria are unioned


def test_dedupe_requirements_exact_pass_still_first() -> None:
    """Casefolded exact duplicates still merge via the first pass with criteria union."""
    requirements = [
        PRDRequirement(id="TEMP-001", title="Password Reset", description="reset flow", acceptance_criteria=["email sent"]),
        PRDRequirement(id="TEMP-002", title="password   reset", description="reset flow", acceptance_criteria=["audit logged"]),
    ]

    deduped = prd_parser._dedupe_requirements(requirements)

    assert [requirement.title for requirement in deduped] == ["Password Reset"]
    assert deduped[0].acceptance_criteria == ["email sent", "audit logged"]


def test_build_normalized_prd_fuzzy_dedupes_chunk_overlap_duplicates(tmp_path) -> None:
    """The same requirement extracted from two chunks with a reworded title yields ONE requirement."""
    source = tmp_path / "prd.md"
    source.write_text(_prd_text(paragraphs=2), encoding="utf-8")

    llm = FakeLLM(
        [
            _requirements_json("User login with email and password"),
            json.dumps([{"title": "User login with email & password", "description": "Duplicate from the overlapping chunk.", "acceptance_criteria": ["wrong password rejected"]}]),
            json.dumps({"project_name": "Auth", "summary": "s"}),
        ]
    )

    prd = asyncio.run(build_normalized_prd(str(source), llm=llm, max_chars_per_chunk=500))

    assert [requirement.title for requirement in prd.requirements] == ["User login with email and password"]
    assert prd.requirements[0].acceptance_criteria == ["User login with email and password works", "wrong password rejected"]
    assert prd.source_metadata["requirement_count"] == 1


def test_build_normalized_prd_stamps_source_metadata_version(tmp_path) -> None:
    """source_metadata['version'] carries the module's normalization version (consumed by test_plan meta)."""
    source = tmp_path / "prd.md"
    source.write_text("# Versioned PRD\n\nUsers can export reports.", encoding="utf-8")

    llm = FakeLLM([_requirements_json("Export Report")])
    prd = asyncio.run(build_normalized_prd(str(source), llm=llm))

    assert prd_parser.NORMALIZED_PRD_VERSION == "1"
    assert prd.source_metadata["version"] == prd_parser.NORMALIZED_PRD_VERSION


# --- json helpers ------------------------------------------------------------------


def test_parse_json_array_skips_bracketed_prose_before_array() -> None:
    real = json.dumps([{"title": "Login"}, {"title": "Logout"}])

    parsed = parse_json_array(f"Sure [see 1]! Here is the array: {real}")

    assert parsed == [{"title": "Login"}, {"title": "Logout"}]


def test_parse_json_object_skips_bracketed_prose_before_object() -> None:
    real = json.dumps({"project_name": "Auth Suite", "summary": "s"})

    parsed = parse_json_object(f"As noted [see 1], requirements: {real}")

    assert parsed == {"project_name": "Auth Suite", "summary": "s"}


def test_parse_json_array_repairs_trailing_commas_and_single_quotes() -> None:
    assert parse_json_array('[{"title": "a"},]') == [{"title": "a"}]
    assert parse_json_array("[{'title': 'b', 'flag': 'yes'},]") == [{"title": "b", "flag": "yes"}]
    # plain text brackets that never parse still raise the usual error
    with pytest.raises(ValueError, match="no JSON array found"):
        parse_json_array("no brackets here at all")


def test_prd_json_roundtrip(tmp_path) -> None:
    prd = NormalizedPRD(
        project_name="Widget Portal",
        summary="A portal for widgets.",
        requirements=[PRDRequirement(id="REQ-001", title="First", description="d", acceptance_criteria=["a1"], source="chunk 1")],
        source_metadata={"chunk_count": 3, "page_count": 2},
    )
    path = str(tmp_path / "prd.json")

    prd_to_json(prd, path)
    loaded = prd_from_json(path)

    assert loaded.model_dump() == prd.model_dump()


# --- pathological-input hardening (fuzz findings) ------------------------------------


def test_parse_json_array_unbalanced_brackets_fails_fast() -> None:
    """A long run of unclosed openers fails fast instead of scanning quadratically (fuzz finding 1)."""
    start = time.perf_counter()
    with pytest.raises(ValueError, match="no JSON array found"):
        parse_json_array("[" * 11000)
    assert time.perf_counter() - start < 0.5


def test_parse_json_array_huge_balanced_nesting_fails_fast() -> None:
    """Balanced but absurdly deep nesting fails fast with the usual ValueError (json.loads recurses per level)."""
    start = time.perf_counter()
    with pytest.raises(ValueError):
        parse_json_array("[" * 100000 + "]" * 100000)
    assert time.perf_counter() - start < 1.0


def test_parse_json_object_large_document_still_parses_within_scan_budget() -> None:
    """A large document with prose junk before the real object still parses (scan budget headroom)."""
    real = json.dumps({"alpha": 1, "beta": [1, 2, 3], "gamma": {"delta": "x" * 50}})
    document = "Preamble. [junk 1] [junk 2] " + ("more prose without brackets. " * 10000) + real

    parsed = parse_json_object(document)

    assert parsed["alpha"] == 1
    assert parsed["gamma"]["delta"] == "x" * 50


def test_strip_html_tags_removes_script_style_comment_blocks() -> None:
    html = "<p>Keep</p><script>alert(1)</script><style>.x { color: red }</style><!-- hidden note --><p>End</p>"

    out = strip_html_tags(html)

    assert "Keep" in out and "End" in out
    assert "alert" not in out and ".x" not in out and "hidden" not in out
    assert "<" not in out
    # A removed block leaves a space behind so adjacent words stay separated.
    assert strip_html_tags("a<script>x</script>b") == "a b"
    assert strip_html_tags("a<!--x-->b") == "a b"
    assert strip_html_tags("a<SCRIPT>x</SCRIPT>b") == "a b"


def test_strip_html_tags_runs_of_unclosed_openers_fail_fast() -> None:
    """Runs of unclosed <script>/<!-- openers complete quickly instead of quadratic backtracking (fuzz finding 3)."""
    start = time.perf_counter()
    strip_html_tags("<script>" * 10000)
    assert time.perf_counter() - start < 0.5

    start = time.perf_counter()
    strip_html_tags("<!--" * 10000)
    assert time.perf_counter() - start < 0.5


# --- concurrent chunk extraction (fuzz finding 2) -------------------------------------


class ConcurrencyTrackingLLM:
    """Async fake LLM that records peak concurrency and answers keyed by the chunk marker in the prompt."""

    def __init__(self, sleep: float = 0.02, fail_markers: set[int] | None = None) -> None:
        self.sleep = sleep
        self.fail_markers = fail_markers or set()
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.calls += 1
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await asyncio.sleep(self.sleep)
            if '"project_name"' in prompt:
                return SimpleNamespace(content=json.dumps({"project_name": "Concurrent PRD", "summary": "s"}))
            marker = int(prompt.rsplit("REQMARKER", 1)[1].split()[0])
            if marker in self.fail_markers:
                return SimpleNamespace(content="no valid json [<bad>] here")
            return SimpleNamespace(content=json.dumps([{"title": f"Chunk {marker} Requirement", "description": "d", "acceptance_criteria": [f"c{marker}"]}]))
        finally:
            self.concurrent -= 1


def _marked_prd_source(tmp_path, paragraphs: int) -> str:
    """One PRD paragraph per chunk (each ~470 chars, max_chars 700 keeps them separate)."""
    body = [f"Section {index}: The system shall provide feature number {index}. " + "detail " * 60 + f" REQMARKER{index}" for index in range(1, paragraphs + 1)]
    source = tmp_path / "prd.md"
    source.write_text("# Concurrent PRD\n\n" + "\n\n".join(body), encoding="utf-8")
    return str(source)


def test_build_normalized_prd_extracts_chunks_concurrently_and_in_order(tmp_path) -> None:
    """Chunk LLM calls run concurrently (bounded by 8) and requirements stay ordered by chunk (fuzz finding 2)."""
    source = _marked_prd_source(tmp_path, paragraphs=18)

    llm = ConcurrencyTrackingLLM()
    prd = asyncio.run(build_normalized_prd(source, llm=llm, max_chars_per_chunk=700))

    assert prd.source_metadata["chunk_count"] == 18
    assert prd.source_metadata["failed_chunks"] == 0
    assert llm.calls == 19  # 18 chunk extractions + 1 summary pass
    assert 2 <= llm.max_concurrent <= 8
    assert [requirement.title for requirement in prd.requirements] == [f"Chunk {index} Requirement" for index in range(1, 19)]
    assert [requirement.id for requirement in prd.requirements] == [f"REQ-{index:03d}" for index in range(1, 19)]


def test_build_normalized_prd_failed_chunk_is_counted_and_order_survives(tmp_path) -> None:
    """A failing middle chunk is counted in failed_chunks and skipped without disturbing chunk order."""
    source = _marked_prd_source(tmp_path, paragraphs=6)

    llm = ConcurrencyTrackingLLM(fail_markers={3})
    prd = asyncio.run(build_normalized_prd(source, llm=llm, max_chars_per_chunk=700))

    assert prd.source_metadata["failed_chunks"] == 1
    assert [requirement.title for requirement in prd.requirements] == [f"Chunk {index} Requirement" for index in (1, 2, 4, 5, 6)]
