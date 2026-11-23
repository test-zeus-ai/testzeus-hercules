"""``hercules import doc``: requirements text from md/txt/pdf/docx, and draft cases (model-drafted or a TODO template)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from testzeus_hercules.cases.importers.common import ImporterError
from testzeus_hercules.cases.importers.doc import DocImportOptions, DraftCases, extract_text, import_doc
from testzeus_hercules.cases.models import TestCase
from testzeus_hercules.cli.main import main

MD = Path(__file__).parent / "fixtures" / "import" / "requirements.md"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path: Path, paragraphs: list[str], *, extra: str = "") -> Path:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>{extra}<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return path


def _cases(project: Path) -> list[TestCase]:
    return [TestCase.model_validate_json(path.read_text()) for path in sorted((project / ".hercules" / "cases").glob("*.json"))]


class FakeDrafter:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.prompts: list[str] = []

    async def complete(self, *, system: str, user: str, schema: type) -> Any:
        self.prompts.append(user)
        if isinstance(self.result, Exception):
            raise self.result
        return schema.model_validate(self.result)


def test_extracts_markdown_and_docx_text(tmp_path: Path) -> None:
    assert "shipping address" in extract_text(MD)
    docx = _docx(tmp_path / "prd.docx", ["Login", "Users can reset a password."])
    assert extract_text(docx) == "Login\n\nUsers can reset a password."


def test_docx_with_a_dtd_is_refused(tmp_path: Path) -> None:
    docx = _docx(tmp_path / "evil.docx", ["x"], extra='<!DOCTYPE d [<!ENTITY a "aaaa">]>')

    with pytest.raises(ImporterError, match="DOCTYPE"):
        extract_text(docx)


def test_pdf_without_text_is_reported(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf = tmp_path / "blank.pdf"
    with pdf.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(ImporterError, match="no text"):
        import_doc(DocImportOptions(doc_path=pdf, project_dir=tmp_path / "p"))


@pytest.mark.parametrize("name", ["notes.exe", "config.yaml", ".env"])
def test_unsupported_or_sensitive_files_are_refused(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text("text")

    with pytest.raises(ImporterError):
        extract_text(path)


def test_without_draft_writes_text_and_a_todo_template_marked_draft(tmp_path: Path) -> None:
    report = import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path))

    text = (tmp_path / ".hercules" / "data" / "docs" / "requirements.txt").read_text()
    assert "Checkout requires a shipping address." in text
    (case,) = _cases(tmp_path)
    assert case.status == "draft" and "draft" in case.tags
    assert all("TODO" in (step.expected or step.action) for step in case.steps)
    assert "shipping address" in (case.description or "")
    assert any("draft" in note.lower() for note in report.notes)


def test_draft_with_a_model_writes_draft_cases(tmp_path: Path) -> None:
    drafter = FakeDrafter(
        {
            "cases": [
                {"title": "Add product to cart", "steps": [{"action": "Sign in"}, {"action": "Add a product", "expected": "Cart shows 1 item"}]},
                {"title": "Add product to cart", "steps": [{"action": "Use {{unknown}} data"}]},
            ]
        }
    )

    import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path, draft=True), drafter_factory=lambda: _ctx(drafter))

    cases = {case.id: case for case in _cases(tmp_path)}
    assert sorted(cases) == ["add-product-to-cart", "add-product-to-cart-2"]
    first = cases["add-product-to-cart"]
    assert first.status == "draft"
    assert [step.action for step in first.steps] == ["Sign in", "Add a product"]
    assert first.steps[1].expected == "Cart shows 1 item"
    assert "{{" not in cases["add-product-to-cart-2"].steps[0].action
    assert "shipping address" in drafter.prompts[0]


def test_draft_falls_back_to_the_template_when_no_model_is_reachable(tmp_path: Path) -> None:
    def unreachable() -> Any:
        raise ImporterError("model server is not reachable")

    report = import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path, draft=True), drafter_factory=unreachable)

    (case,) = _cases(tmp_path)
    assert case.status == "draft" and any("TODO" in step.action for step in case.steps)
    assert any("not reachable" in note for note in report.notes)


def test_draft_falls_back_when_the_model_call_fails(tmp_path: Path) -> None:
    drafter = FakeDrafter(RuntimeError("boom"))

    report = import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path, draft=True), drafter_factory=lambda: _ctx(drafter))

    assert len(_cases(tmp_path)) == 1
    assert any("RuntimeError: boom" in note for note in report.notes)


@pytest.mark.parametrize(
    "answer",
    [
        {"title": "Apply a coupon", "steps": [{"action": "Fill PANTRY10 in Coupon and click Apply"}]},
        [{"title": "Apply a coupon", "steps": [{"action": "Fill PANTRY10 in Coupon and click Apply"}]}],
    ],
    ids=["one-case", "a-list"],
)
def test_a_draft_given_as_one_case_or_a_bare_list_is_taken(tmp_path: Path, answer: Any) -> None:
    drafter = FakeDrafter(answer)

    import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path, draft=True), drafter_factory=lambda: _ctx(drafter))

    (case,) = _cases(tmp_path)
    assert case.title == "Apply a coupon" and case.status == "draft" and not any("TODO" in step.action for step in case.steps)


def test_dry_run_and_conflicts(tmp_path: Path) -> None:
    import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path, dry_run=True))
    assert not (tmp_path / ".hercules").exists()

    import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path))
    report = import_doc(DocImportOptions(doc_path=MD, project_dir=tmp_path))
    assert any("--force" in item.reason for item in report.skipped)


def test_draft_schema_is_bounded() -> None:
    assert DraftCases.model_json_schema()["properties"]["cases"]["maxItems"] == 20


def test_cli_import_doc(tmp_path: Path) -> None:
    out = io.StringIO()

    code = main(["import", "doc", str(MD), "--project", str(tmp_path)], env={"HOME": str(tmp_path)}, stdout=out, stderr=out)

    assert code == 0, out.getvalue()
    assert "requirements-draft.json" in out.getvalue()


class _ctx:
    """An async context manager yielding a drafter, like the real model connection."""

    def __init__(self, drafter: FakeDrafter) -> None:
        self.drafter = drafter

    async def __aenter__(self) -> FakeDrafter:
        return self.drafter

    async def __aexit__(self, *exc: object) -> None:
        return None


def test_a_private_key_renamed_to_a_document_is_refused(tmp_path: Path) -> None:
    key = tmp_path / "notes.txt"
    key.write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
    with pytest.raises(ImporterError, match="private key"):
        extract_text(key)
