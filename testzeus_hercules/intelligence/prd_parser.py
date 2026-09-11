"""PRD ingestion: extract text from files/URLs, chunk it, and use an LLM to build a :class:`NormalizedPRD`.

The module is deliberately dependency-light at import time: heavy dependencies
(``pypdf``, ``unstructured``, the LiteLLM chat model factory) are imported
lazily inside the functions that need them.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import zipfile
from typing import Any, Callable, Iterator
from xml.etree import ElementTree

import httpx
from testzeus_hercules.intelligence.normalized_prd import NormalizedPRD, PRDRequirement
from testzeus_hercules.utils.logger import logger

_HTML_ENTITY_RE = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")
_LAZY_SCAN_WINDOW = 100_000  # max chars searched for a block closer after an opener
_SCRIPT_STYLE_OPEN_RE = re.compile(r"<(script|style)\b", re.IGNORECASE)
_SCRIPT_STYLE_CLOSE_RES = {"script": re.compile(r"</script>", re.IGNORECASE), "style": re.compile(r"</style>", re.IGNORECASE)}
_HTML_COMMENT_OPEN_RE = re.compile(r"<!--")
_HTML_COMMENT_CLOSE_RE = re.compile(r"-->")
_TAG_RE = re.compile(r"<[^>]+>")
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$", re.MULTILINE)

_BASIC_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}

# LLM calls allowed in flight while extracting requirements from PRD chunks.
_MAX_CONCURRENT_CHUNK_CALLS = 8

# Bounds for the balanced-segment scan in _iter_balanced_segments: without them a
# text full of unclosed openers (e.g. "[" * 11000) makes every candidate rescan
# forward to the end, which is quadratic in the text length. The budget caps the
# TOTAL characters scanned across all candidates and the cap limits how many
# candidates are inspected; tripping either simply stops yielding, which sends
# the caller down the existing "could not parse" ValueError path.
_JSON_SCAN_CHAR_BUDGET = 2_000_000
_JSON_SCAN_MAX_CANDIDATES = 64

# Version stamped into every NormalizedPRD's source_metadata["version"]; consumed
# downstream by test_plan.generate_test_plan (plan meta["version"]).
NORMALIZED_PRD_VERSION = "1"

# Near-identical title merge threshold for the fuzzy dedupe pass in
# _dedupe_requirements (token-set Jaccard on casefolded titles). 0.85 keeps
# genuinely different requirements apart while catching chunk-overlap
# duplicates whose wording was slightly reworded by the LLM.
_FUZZY_DEDUPE_THRESHOLD = 0.85
_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")


def strip_code_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ```) from an LLM response."""
    cleaned = str(text or "").strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```[a-zA-Z0-9_-]*", "", cleaned).replace("```", "").strip()
    return cleaned


def _iter_balanced_segments(text: str, open_char: str, close_char: str) -> Iterator[str]:
    """Yield every balanced ``open_char ... close_char`` segment (string-aware).

    Leading prose containing brackets (e.g. ``"As noted [see 1], requirements: [...]"``)
    produces several candidates; every one is yielded so callers can try parsing each.

    The scan is bounded by ``_JSON_SCAN_CHAR_BUDGET`` total characters across all
    candidates and ``_JSON_SCAN_MAX_CANDIDATES`` candidates, so pathological inputs
    (long runs of unclosed openers) fail fast instead of scanning quadratically.
    """
    text_length = len(text)
    budget = _JSON_SCAN_CHAR_BUDGET
    candidates = 0
    for match in re.finditer(re.escape(open_char), text):
        if candidates >= _JSON_SCAN_MAX_CANDIDATES or budget <= 0:
            return
        start = match.start()
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, text_length):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    budget -= index - start + 1
                    candidates += 1
                    yield text[start : index + 1]
                    break
        else:
            # Opener never closed: the inner loop ran to the end of the text.
            budget -= text_length - start
            candidates += 1


_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_SINGLE_QUOTED_STRING_RE = re.compile(r"'([^'\\]*(?:\\.[^'\\]*)*)'")


def _repair_json_segment(segment: str) -> str:
    """Best-effort repair of common LLM JSON mistakes (trailing commas, single quotes)."""
    repaired = _TRAILING_COMMA_RE.sub(r"\1", segment)
    repaired = _SINGLE_QUOTED_STRING_RE.sub(lambda match: json.dumps(match.group(1)), repaired)
    return repaired


def _parse_typed_segment(cleaned: str, open_char: str, close_char: str, expected: type, expected_name: str, context: str) -> Any:
    """Parse the first candidate balanced segment that yields ``expected``.

    Tries every candidate strictly first, then a tolerant repair pass (trailing
    commas, single-quoted strings) before giving up.
    """
    segments = list(_iter_balanced_segments(cleaned, open_char, close_char))
    if not segments:
        raise ValueError(f"{context}: no JSON {expected_name} found in response: {cleaned[:200]!r}")

    last_error: Exception | None = None
    for segment in segments:
        try:
            parsed = json.loads(segment)
        except (json.JSONDecodeError, RecursionError) as exc:
            # RecursionError: a balanced but absurdly deep segment (json.loads recurses per level).
            last_error = exc
            continue
        if isinstance(parsed, expected):
            return parsed

    for segment in segments:
        repaired = _repair_json_segment(segment)
        if repaired == segment:
            continue
        try:
            parsed = json.loads(repaired)
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(parsed, expected):
            return parsed

    if last_error is not None:
        raise ValueError(f"{context}: response contains an invalid JSON {expected_name}: {last_error}") from last_error
    raise ValueError(f"{context}: expected a JSON {expected_name}, but no candidate segment had the expected type")


def parse_json_array(content: str, context: str = "LLM response") -> list[Any]:
    """Defensively parse a JSON array out of an LLM response.

    Strips code fences, then tries every balanced ``[...]`` segment (skipping
    bracketed prose before the real array) until one parses as a list; falls
    back to a tolerant repair for trailing commas and single quotes. Raises
    :class:`ValueError` with a clear message when no valid array is found.
    """
    cleaned = strip_code_fences(content)
    parsed = _parse_typed_segment(cleaned, "[", "]", list, "array", context)
    return parsed


def parse_json_object(content: str, context: str = "LLM response") -> dict[str, Any]:
    """Defensively parse a JSON object out of an LLM response (see :func:`parse_json_array`)."""
    cleaned = strip_code_fences(content)
    parsed = _parse_typed_segment(cleaned, "{", "}", dict, "object", context)
    return parsed


def _decode_entities(text: str) -> str:
    """Replace the common HTML entities; leave unknown entities untouched."""

    def _replace(match: re.Match[str]) -> str:
        entity = match.group(1)
        if entity in _BASIC_ENTITIES:
            return _BASIC_ENTITIES[entity]
        if entity.startswith(("#", "#x", "#X")):
            try:
                return chr(int(entity[2:], 16) if entity[1] in ("x", "X") else int(entity[1:]))
            except (ValueError, OverflowError):
                return match.group(0)
        return match.group(0)

    return _HTML_ENTITY_RE.sub(_replace, text)


def _remove_matched_blocks(text: str, opener_re: re.Pattern[str], close_re_for: Callable[[re.Match[str]], re.Pattern[str]]) -> str:
    """Remove ``opener ... closer`` blocks using a window-bounded closer search.

    Equivalent to a lazy ``opener.*?closer`` regex removal for well-formed
    documents, but the closer search after an opener is capped at
    ``_LAZY_SCAN_WINDOW`` characters, so runs of unclosed openers (e.g.
    ``"<script>" * 10000``) cannot trigger quadratic regex backtracking. An
    opener whose closer is not found within the window is left in place and is
    stripped later as a plain tag.
    """
    parts: list[str] = []
    write_position = 0
    scan_position = 0
    # Positions below this index were already searched and hold no closer, so
    # later openers can start their closer search here instead of rescanning.
    closer_floor = 0
    while True:
        match = opener_re.search(text, scan_position)
        if match is None:
            break
        closer = close_re_for(match)
        close_match = closer.search(text, max(match.end(), closer_floor), match.end() + _LAZY_SCAN_WINDOW)
        if close_match is None:
            scan_position = match.end()
            closer_floor = match.end() + _LAZY_SCAN_WINDOW
            continue
        # Replace the whole block with a single space, exactly like the previous
        # lazy-regex ``re.sub(" ", ...)`` did.
        parts.append(text[write_position : match.start()] + " ")
        write_position = close_match.end()
        scan_position = close_match.end()
        closer_floor = close_match.end()
    parts.append(text[write_position:])
    return "".join(parts)


def strip_html_tags(html: str) -> str:
    """Naively strip tags from an HTML document and normalize whitespace."""
    without_scripts = _remove_matched_blocks(html, _SCRIPT_STYLE_OPEN_RE, lambda match: _SCRIPT_STYLE_CLOSE_RES[match.group(1).lower()])
    without_comments = _remove_matched_blocks(without_scripts, _HTML_COMMENT_OPEN_RE, lambda match: _HTML_COMMENT_CLOSE_RE)
    block_breaks = without_comments.replace("</p>", "\n\n").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _TAG_RE.sub(" ", block_breaks)
    text = _decode_entities(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n\n".join(line for line in lines if line).strip()


def _read_text_file(path: str) -> str:
    """Read a file as UTF-8 text, replacing undecodable bytes instead of failing."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _pdf_text(path: str, metadata: dict[str, Any]) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
        metadata["page_count"] = len(reader.pages)
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        # Re-raise as ValueError (e.g. pypdf.errors.PdfStreamError would otherwise
        # escape callers that only handle ValueError/OSError).
        raise ValueError(f"could not parse PDF file {path}: {exc}") from exc
    return "\n\n".join(page for page in pages if page).strip()


def _docx_text_unstructured(path: str) -> str:
    """Extract docx text via ``unstructured`` (imported lazily; raises on failure)."""
    from unstructured.partition.docx import partition_docx

    elements = partition_docx(filename=path)
    parts = []
    for element in elements:
        text = getattr(element, "text", None) or str(element)
        text = text.strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text_fallback(path: str) -> str:
    """Extract docx text with only the stdlib: unzip and parse ``word/document.xml``."""
    with zipfile.ZipFile(path) as archive:
        with archive.open("word/document.xml") as handle:
            tree = ElementTree.parse(handle)
    paragraphs = []
    for paragraph in tree.iter(f"{_WORD_NS}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{_WORD_NS}t")]
        text = "".join(texts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _docx_text(path: str) -> str:
    """Extract docx text, preferring ``unstructured`` and falling back to a zipfile/XML parse."""
    try:
        text = _docx_text_unstructured(path)
        if text.strip():
            return text
    except Exception as exc:
        logger.warning("unstructured docx extraction failed for %s (%s); using zipfile fallback", path, exc)
    try:
        return _docx_text_fallback(path)
    except Exception as exc:
        # Re-raise as ValueError (e.g. zipfile.BadZipFile would otherwise escape
        # callers that only handle ValueError/OSError).
        raise ValueError(f"could not parse DOCX file {path}: {exc}") from exc


def _fetch_url_text(url: str, metadata: dict[str, Any]) -> str:
    try:
        response = httpx.get(url, follow_redirects=True, timeout=30)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"Failed to fetch PRD source URL {url}: {exc}") from exc
    metadata["url"] = url
    metadata["http_status_code"] = response.status_code
    raw = response.text
    stripped = strip_html_tags(raw)
    return stripped if stripped else raw.strip()


def extract_text(source: str, metadata: dict[str, Any] | None = None) -> str:
    """Extract raw text from a PRD source (local file or http(s) URL).

    Dispatches by extension: ``.pdf`` via pypdf, ``.docx`` via unstructured
    (zipfile/XML fallback), everything else is read as text. URL sources are
    fetched with httpx and naively tag-stripped. Metadata about the extraction
    (source type, file type, page count, ...) is written into ``metadata`` when
    a dict is supplied.
    """
    meta = metadata if metadata is not None else {}
    meta["source"] = source

    if source.startswith(("http://", "https://")):
        meta["source_type"] = "url"
        meta["file_type"] = "html"
        return _fetch_url_text(source, meta)

    if not os.path.exists(source):
        raise FileNotFoundError(f"PRD source not found (neither a URL nor an existing file): {source}")
    if os.path.isdir(source):
        raise ValueError(f"PRD source is a directory, expected a file or URL: {source}")

    meta["source_type"] = "file"
    extension = os.path.splitext(source)[1].lower().lstrip(".")
    meta["file_type"] = extension or "txt"

    if extension == "pdf":
        text = _pdf_text(source, meta)
    elif extension == "docx":
        text = _docx_text(source)
    else:
        text = _read_text_file(source)
    return text.strip()


def chunk_text(text: str, max_chars: int = 8000, overlap: int = 400) -> list[str]:
    """Split ``text`` into paragraph-aware chunks of at most ``max_chars`` characters.

    Chunks are assembled greedily from whole paragraphs (split on blank lines;
    oversized paragraphs are split on single newlines, then hard-wrapped with a
    built-in ``overlap`` step so boundary text appears in both pieces). Each
    new chunk starts with an ``overlap``-character tail of the previous chunk so
    that requirements spanning chunk boundaries are not lost.

    Raises:
        ValueError: If ``max_chars`` or ``overlap`` is not an integer, or if
            ``max_chars`` is not positive.
    """
    if not isinstance(max_chars, int):
        raise ValueError("max_chars must be an integer")
    if not isinstance(overlap, int):
        raise ValueError("overlap must be an integer")
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    overlap = max(0, min(overlap, max_chars // 2))

    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", normalized) if paragraph.strip()]

    pieces: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        for line in lines:
            if len(line) <= max_chars:
                pieces.append(line)
            else:
                # Hard-split with built-in overlap (step < max_chars) so boundary
                # text appears in both pieces; otherwise a requirement straddling
                # the seam would be truncated exactly when the chunk-level overlap
                # tail computation has no room left.
                step = max(1, max_chars - overlap)
                pieces.extend(line[i : i + max_chars] for i in range(0, len(line), step))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            allowed = max_chars - len(piece) - len("\n\n")
            tail = current[-overlap:] if overlap and allowed > 0 else ""
            if tail and len(tail) > allowed:
                tail = tail[-allowed:]
            current = tail
            candidate = f"{current}\n\n{piece}" if current else piece
        current = candidate
    if current:
        chunks.append(current)
    return chunks


_REQUIREMENT_EXTRACTION_PROMPT = """You are a senior product analyst extracting testable product requirements from a PRD.

Below is one excerpt (chunk) of a Product Requirements Document.
Extract every user-visible, testable product requirement described in this excerpt.

Return ONLY a pure JSON array (no prose, no markdown fences) where each element is an object:
[
  {{
    "title": "short requirement title",
    "description": "one or two sentence description of the requirement",
    "acceptance_criteria": ["criterion 1", "criterion 2"]
  }}
]

Rules:
- Only include requirements actually described in the excerpt; never invent any.
- Ignore boilerplate, tables of contents, changelogs, legal text and meta commentary.
- If the excerpt contains no requirements, return an empty array: []

<chunk>
{chunk}
</chunk>"""


async def _extract_requirements_from_chunk(chunk: str, llm: Any, chunk_index: int) -> list[PRDRequirement]:
    """Ask the LLM for the requirements in one chunk; returns requirements with temporary ids.

    Raises :class:`ValueError` when the response cannot be parsed as a JSON array.
    """
    prompt = _REQUIREMENT_EXTRACTION_PROMPT.format(chunk=chunk)
    response = await llm.ainvoke(prompt)
    content = getattr(response, "content", response)
    if not isinstance(content, str):
        content = str(content)
    items = parse_json_array(content, context=f"chunk {chunk_index + 1} requirement extraction")

    requirements: list[PRDRequirement] = []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            logger.warning("Chunk %d: skipping non-object requirement entry: %r", chunk_index + 1, item)
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            logger.warning("Chunk %d: skipping requirement entry without a title (index %d)", chunk_index + 1, item_index)
            continue
        description = str(item.get("description") or "").strip()
        raw_criteria = item.get("acceptance_criteria")
        criteria: list[str] = []
        if isinstance(raw_criteria, list):
            criteria = [str(criterion).strip() for criterion in raw_criteria if str(criterion).strip()]
        elif isinstance(raw_criteria, str):
            # Tolerate LLMs that return a single string: keep it whole when it is
            # one line, otherwise split it on newlines and strip bullet markers.
            text = raw_criteria.strip()
            if text and "\n" not in text:
                criteria = [text]
            else:
                for line in text.splitlines():
                    line = line.strip().lstrip("-*•").strip()
                    if line:
                        criteria.append(line)
        requirements.append(
            PRDRequirement(
                id=f"TEMP-{chunk_index + 1:03d}-{item_index + 1:03d}",
                title=title,
                description=description,
                acceptance_criteria=criteria,
                source=f"chunk {chunk_index + 1}",
            )
        )
    return requirements


def _fallback_project_name(text: str, source: str) -> str:
    """Derive a project name from the first markdown heading, else the file name."""
    heading = _MARKDOWN_HEADING_RE.search(text or "")
    if heading:
        return heading.group(1).strip()[:120]
    stem = os.path.splitext(os.path.basename(source.rstrip("/")))[0]
    return stem.strip() or "Unnamed Project"


_SUMMARY_PROMPT = """You are a senior product analyst.

Below is the beginning of a Product Requirements Document.
Write a one-paragraph summary of the project and give the project a concise name.

Return ONLY a pure JSON object (no prose, no markdown fences):
{{
  "project_name": "short project name",
  "summary": "one paragraph summary of what the project does"
}}

<prd>
{prd}
</prd>"""


async def _summarize_prd(text: str, llm: Any) -> tuple[str, str]:
    """Best-effort LLM pass producing ``(project_name, summary)``; returns ("", "") on any failure."""
    try:
        prompt = _SUMMARY_PROMPT.format(prd=text[:6000])
        response = await llm.ainvoke(prompt)
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            content = str(content)
        parsed = parse_json_object(content, context="PRD summarization")
        project_name = str(parsed.get("project_name") or "").strip()
        summary = str(parsed.get("summary") or "").strip()
        return project_name, summary
    except Exception as exc:
        logger.warning("PRD summarization pass failed (%s); falling back to heading/filename", exc)
        return "", ""


def _token_set(text: str) -> frozenset[str]:
    """Casefolded alphanumeric token set of ``text``; ``&`` is folded to ``and`` first (titles use both interchangeably)."""
    return frozenset(_TITLE_TOKEN_RE.findall(text.casefold().replace("&", " and ")))


def _token_set_jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard similarity of two token sets; 0.0 when either side is empty."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _dedupe_requirements(requirements: list[PRDRequirement]) -> list[PRDRequirement]:
    """Drop duplicate requirements, merging unique acceptance criteria into the kept one.

    Two passes, both greedy first-match and order-preserving:

    1. exact: casefolded, whitespace-normalized title equality;
    2. fuzzy: token-set Jaccard similarity >= 0.85 between casefolded titles
       (see ``_FUZZY_DEDUPE_THRESHOLD``), so the same requirement extracted from
       two overlapping chunks with slightly reworded titles ("User login with
       email and password" vs "User login with email & password") does not
       survive twice and double the generated test cases.

    Merges keep the first requirement's id/title/description and union the
    acceptance criteria. Genuinely different requirements stay far below the
    threshold ("Login" vs "Logout" scores 0.0), so they are never merged.
    """
    deduped: list[PRDRequirement] = []
    seen: dict[str, PRDRequirement] = {}
    for requirement in requirements:
        key = " ".join(requirement.title.split()).casefold()
        existing = seen.get(key)
        if existing is None:
            seen[key] = requirement
            deduped.append(requirement)
            continue
        for criterion in requirement.acceptance_criteria:
            if criterion not in existing.acceptance_criteria:
                existing.acceptance_criteria.append(criterion)

    kept: list[tuple[frozenset[str], PRDRequirement]] = []
    for requirement in deduped:
        tokens = _token_set(requirement.title)
        match = next((candidate for candidate_tokens, candidate in kept if _token_set_jaccard(tokens, candidate_tokens) >= _FUZZY_DEDUPE_THRESHOLD), None)
        if match is None:
            kept.append((tokens, requirement))
            continue
        for criterion in requirement.acceptance_criteria:
            if criterion not in match.acceptance_criteria:
                match.acceptance_criteria.append(criterion)
    return [requirement for _, requirement in kept]


async def build_normalized_prd(source: str, llm: Any = None, max_chars_per_chunk: int = 8000) -> NormalizedPRD:
    """Build a :class:`NormalizedPRD` from a PRD file/URL.

    Extracts text, chunks it, extracts requirements per chunk with the LLM in
    bounded concurrency (at most ``_MAX_CONCURRENT_CHUNK_CALLS`` calls in
    flight; chunks that fail are logged, counted and skipped), dedupes
    near-identical titles, renumbers ids to ``REQ-001...``, and runs a
    best-effort summary pass.

    The synchronous extraction (network fetch, PDF/DOCX parsing) runs in a
    worker thread via ``asyncio.to_thread`` so the event loop is never blocked.

    ``prd.source_metadata`` always carries ``chunk_count``, ``failed_chunks``
    and ``version`` (``NORMALIZED_PRD_VERSION``, consumed as the test plan's
    meta version): when ``failed_chunks`` is non-zero some chunk extractions
    were skipped, so the requirement set may be incomplete even though the call
    succeeded. When every chunk fails (zero requirements survive), a
    :class:`ValueError` is raised mentioning the failed chunk count.
    """
    if llm is None:
        from testzeus_hercules.utils.litellm_helper import get_litellm_chat_model

        llm = get_litellm_chat_model("planner_agent")

    metadata: dict[str, Any] = {"version": NORMALIZED_PRD_VERSION}
    text = await asyncio.to_thread(extract_text, source, metadata)
    if not text.strip():
        raise ValueError(f"No text content could be extracted from PRD source: {source}")

    chunks = chunk_text(text, max_chars=max_chars_per_chunk)
    metadata["chunk_count"] = len(chunks)

    extracted: list[PRDRequirement] = []
    failed_chunks = 0
    active_chunks = [(index, chunk) for index, chunk in enumerate(chunks) if chunk.strip()]

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHUNK_CALLS)

    async def _extract_chunk(index: int, chunk: str) -> list[PRDRequirement]:
        async with semaphore:
            logger.info("Extracting requirements from chunk %d/%d of %s", index + 1, len(chunks), source)
            return await _extract_requirements_from_chunk(chunk, llm, index)

    # Extract concurrently (bounded by the semaphore) instead of one serialized
    # LLM call per chunk; gather preserves chunk order, so requirements stay
    # ordered by their source chunk. Failures are collected like the previous
    # per-chunk try/except did: counted, logged and skipped.
    results = await asyncio.gather(*(_extract_chunk(index, chunk) for index, chunk in active_chunks), return_exceptions=True)
    for (index, _chunk), result in zip(active_chunks, results):
        if isinstance(result, BaseException):
            failed_chunks += 1
            logger.warning("Skipping chunk %d/%d: requirement extraction failed: %s", index + 1, len(chunks), result)
            continue
        extracted.extend(result)
    metadata["failed_chunks"] = failed_chunks

    requirements = _dedupe_requirements(extracted)
    if not requirements:
        if failed_chunks:
            raise ValueError(f"No requirements could be extracted from PRD source: {source} " f"({failed_chunks} of {len(chunks)} chunk extractions failed; see source_metadata.failed_chunks)")
        raise ValueError(f"No requirements could be extracted from PRD source: {source}")

    for index, requirement in enumerate(requirements, start=1):
        requirement.id = f"REQ-{index:03d}"
    metadata["requirement_count"] = len(requirements)

    project_name, summary = await _summarize_prd(text, llm)
    if not project_name:
        project_name = _fallback_project_name(text, source)

    return NormalizedPRD(project_name=project_name, summary=summary, requirements=requirements, source_metadata=metadata)
