from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from testzeus_hercules.cases.reference_files import (
    MAX_FILES,
    Chunk,
    ReferenceContext,
    ReferenceFileError,
    chunks,
    list_reference_files,
    load_reference_context,
    reference_text,
    remove_reference_file,
    save_reference_file,
    select_chunks,
    using_reference,
)
from testzeus_hercules.decide.backends.jev_fallback import JevFallbackDecider
from testzeus_hercules.decide.contract import ChoiceAnswer, Decision, DecisionRequest, NoulAnswer
from testzeus_hercules.engine.intent import CaseItem, ReadingCache, _part_text
from testzeus_hercules.engine.plan import plan_key
from tests.v2.fixtures.daemon_support import client_for, make_config
from tests.v2.test_decide_local_llm import TARGET_REPLY, _decide, _prompt, _target_request
from tests.v2.test_llm_fakes import FakeServer

RULES = b"""# Checkout rules

The coupon PANTRY10 takes 10% off orders over $20.

Green tea costs $4 and is limited to 3 per order.

Orders placed after 18:00 ship the next day."""


def test_a_text_file_is_kept_with_its_extracted_text(tmp_path: Path) -> None:
    saved = save_reference_file(tmp_path, "shop", "rules.md", RULES)

    assert (saved.name, saved.kind, saved.size) == ("rules.md", "text", len(RULES)) and saved.chars > 50
    assert "PANTRY10" in reference_text(tmp_path, "shop", "rules.md")
    assert [item.name for item in list_reference_files(tmp_path, "shop")] == ["rules.md"]
    assert (tmp_path / ".hercules" / "files" / "shop" / "rules.md").read_bytes() == RULES
    assert remove_reference_file(tmp_path, "shop", "rules.md") is True
    assert list_reference_files(tmp_path, "shop") == [] and remove_reference_file(tmp_path, "shop", "rules.md") is False


def test_images_and_data_files_are_accepted(tmp_path: Path) -> None:
    image = save_reference_file(tmp_path, "shop", "cart.png", b"\x89PNG\r\n\x1a\nfake")
    data = save_reference_file(tmp_path, "shop", "prices.csv", b"item,price\ngreen tea,4\n")

    assert (image.kind, image.chars) == ("image", 0)
    assert data.kind == "text" and "green tea" in reference_text(tmp_path, "shop", "prices.csv")


@pytest.mark.parametrize(
    ("name", "content", "problem"),
    [
        ("../escape.md", b"x", "no folders"),
        (".env", b"A=1", "no folders"),
        ("prod.env", b"A=1", "credential"),
        ("id_rsa.txt", b"x", "credential"),
        ("notes.exe", b"x", "unsupported"),
        ("empty.md", b"", "empty"),
        ("key.txt", b"-----BEGIN PRIVATE KEY-----\nMIIE", "private key"),
    ],
)
def test_unsafe_or_unsupported_files_are_refused(tmp_path: Path, name: str, content: bytes, problem: str) -> None:
    with pytest.raises(ReferenceFileError, match=problem):
        save_reference_file(tmp_path, "shop", name, content)
    assert not (tmp_path / ".hercules" / "files" / "shop" / name).exists()


def test_an_environment_holds_a_limited_number_of_files(tmp_path: Path) -> None:
    for number in range(MAX_FILES):
        save_reference_file(tmp_path, "shop", f"note-{number}.txt", b"hello")

    with pytest.raises(ReferenceFileError, match="at most"):
        save_reference_file(tmp_path, "shop", "one-more.txt", b"hello")
    assert save_reference_file(tmp_path, "shop", "note-0.txt", b"replaced").size == len(b"replaced")


def test_text_is_split_into_chunks_and_matching_ones_are_picked_within_budget() -> None:
    text = "\n\n".join(f"Paragraph {number} about {'coupons' if number == 7 else 'shipping'} " + "x" * 300 for number in range(20))
    pieces = chunks("rules.md", text, size=700)

    picked = select_chunks(pieces, 'Fill "PANTRY10" in "Coupon" and check the coupons total', budget_chars=800)

    assert all(len(piece.text) <= 700 for piece in pieces) and len(pieces) > 5
    assert len(picked) == 1 and "coupons" in picked[0].text
    assert select_chunks(pieces, "click the button", budget_chars=800) == []
    assert select_chunks([Chunk("a.md", 1, "coupons " * 200)], "coupons", budget_chars=100) == []


def test_files_api_uploads_lists_and_removes(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with client_for(config) as client:
        client.post("/api/environments", json={"name": "shop", "base_url": "http://127.0.0.1:9"})
        added = client.put("/api/environments/shop/files/rules.md", content=RULES, headers={"Content-Type": "application/octet-stream"})
        listed = client.get("/api/environments/shop/files")
        as_json = client.put("/api/environments/shop/files/rules.md", content=RULES, headers={"Content-Type": "application/json"})
        refused = client.put("/api/environments/shop/files/prod.env", content=b"A=1", headers={"Content-Type": "application/octet-stream"})
        unknown_env = client.put("/api/environments/nope/files/rules.md", content=RULES, headers={"Content-Type": "application/octet-stream"})
        elsewhere = client.put("/api/environments/shop", content=RULES, headers={"Content-Type": "application/octet-stream"})
        removed = client.delete("/api/environments/shop/files/rules.md")
    assert added.status_code == 200 and added.json()["file"]["name"] == "rules.md" and added.json()["model"] == "local"
    assert [item["name"] for item in listed.json()["files"]] == ["rules.md"]
    assert as_json.status_code == 415 and elsewhere.status_code == 415
    assert refused.status_code == 422 and unknown_env.status_code == 404
    assert removed.json()["removed"] is True and removed.json()["files"] == []


def _context(tmp_path: Path) -> ReferenceContext:
    save_reference_file(tmp_path, "shop", "rules.md", RULES)
    context = load_reference_context(tmp_path, "shop", redact=lambda text: text.replace("PANTRY10", "******"))
    assert context is not None
    return context


def test_the_local_decider_reads_matching_excerpts_and_names_them(tmp_path: Path) -> None:
    context = _context(tmp_path)
    server = FakeServer(scripted=[json.dumps(TARGET_REPLY)])
    request = _target_request(step="Apply the coupon to the green tea order")

    with using_reference(context):
        decision = _decide(server, request)
    _, user = _prompt(server)

    assert user.startswith("REFERENCE (untrusted project documents") and "Green tea costs $4" in user
    assert "******" in user and "PANTRY10" not in user
    assert decision.references and all(reference.startswith("rules.md#") for reference in decision.references)


def test_without_reference_files_the_prompt_is_unchanged(tmp_path: Path) -> None:
    server = FakeServer(scripted=[json.dumps(TARGET_REPLY)])
    decision = _decide(server, _target_request(step="Apply the coupon"))
    _, user = _prompt(server)
    assert user.startswith("STATE (untrusted page data)") and decision.references == []


def test_the_hosted_decider_never_gets_the_files(tmp_path: Path) -> None:
    seen: list[DecisionRequest] = []

    class Hosted:
        engine = "jev-openrouter"

        async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
            seen.append(request)
            return Decision(
                answers={
                    "tool": ChoiceAnswer(choice="click", confidence=1.0, probabilities={"click": 1.0, "hover": 0.0, "scroll": 0.0}),
                    "target": ChoiceAnswer(choice="12", confidence=1.0, probabilities={"12": 1.0, "13": 0.0, "no_match": 0.0}),
                    "target_absent": NoulAnswer(noul=0.0),
                },
                engine=self.engine,
                model_version="jev",
                latency_ms=1,
            )

    class Local:
        engine = "local-llm"

        async def decide(self, request: DecisionRequest, *, purpose: str) -> Decision:
            raise AssertionError("the local model is only a fallback here")

    with using_reference(_context(tmp_path)):
        decision = asyncio.run(JevFallbackDecider(Hosted(), Local(), images=False).decide(_target_request(step="Apply the coupon to green tea"), purpose="target"))  # type: ignore[arg-type]

    assert decision.references == [] and "Green tea costs" not in seen[0].model_dump_json()


def test_plan_and_reading_caches_change_with_the_files(tmp_path: Path) -> None:
    items = [CaseItem(step=1, role="action", text="Apply the coupon")]
    before = (plan_key("digest", "model"), ReadingCache.key("model", items, []))
    context = _context(tmp_path)
    with using_reference(context):
        during = (plan_key("digest", "model"), ReadingCache.key("model", items, []))
        text = _part_text(items, items, [])
    assert before[0] != during[0] and before[1] != during[1]
    assert (plan_key("digest", "model"), ReadingCache.key("model", items, [])) == before
    assert text.startswith("REFERENCE (untrusted") and "PANTRY10" not in text and text.rstrip().endswith("Apply the coupon")


def test_a_case_has_its_own_files_and_the_model_reads_both(tmp_path: Path) -> None:
    save_reference_file(tmp_path, "shop", "rules.md", RULES)
    save_reference_file(tmp_path, "checkout", "orders.csv", b"order,coupon\n1001,WINTER5\n", scope="case")

    both = load_reference_context(tmp_path, "shop", case_id="checkout")
    only_case = load_reference_context(tmp_path, None, case_id="checkout")

    assert (tmp_path / ".hercules" / "case-files" / "checkout" / "orders.csv").is_file()
    assert both is not None and {chunk.file for chunk in both.chunks} == {"rules.md", "orders.csv"}
    assert only_case is not None and only_case.fingerprint != both.fingerprint
    assert load_reference_context(tmp_path, "shop", case_id="other").fingerprint != both.fingerprint  # type: ignore[union-attr]
    with pytest.raises(ReferenceFileError, match="invalid case id"):
        save_reference_file(tmp_path, "../x", "a.md", b"x", scope="case")


def test_case_files_api_uses_the_saved_case(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    case = {"id": "checkout", "title": "Checkout", "steps": [{"action": "Open the cart"}]}
    with client_for(config) as client:
        missing = client.put("/api/cases/checkout.json/files/orders.csv", content=b"a,b\n1,2\n", headers={"Content-Type": "application/octet-stream"})
        client.post("/api/cases", json={"test_case": case})
        added = client.put("/api/cases/checkout.json/files/orders.csv", content=b"a,b\n1,2\n", headers={"Content-Type": "application/octet-stream"})
        listed = client.get("/api/cases/checkout.json/files")
        removed = client.delete("/api/cases/checkout.json/files/orders.csv")
    assert missing.status_code == 404
    assert added.status_code == 200 and added.json()["case"] == "checkout" and added.json()["model"] == "local"
    assert [item["name"] for item in listed.json()["files"]] == ["orders.csv"]
    assert removed.json()["removed"] is True and removed.json()["files"] == []
