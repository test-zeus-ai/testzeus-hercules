"""Bundled axe-core: shipped unmodified as package data, injected per frame, never fetched from the network."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

import pytest
from testzeus_hercules.perceive import take_snapshot
from testzeus_hercules.quality.axe import DEFAULT_TAGS, axe_info, axe_source, level_of, run_axe, wcag_of

from tests.v2.browser_fixtures import BrowserHarness, browser  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
QUALITY = Path(__file__).resolve().parent / "fixtures" / "quality"
VENDOR = ROOT / "testzeus_hercules" / "quality" / "vendor" / "axe"
FIXTURE_HOST = "http://quality.test/"


def test_bundle_is_pinned_and_unmodified() -> None:
    info = axe_info()
    assert info["version"] == "4.10.3"
    assert info["sha256(axe.min.js)"] == info["sha256_actual"] == hashlib.sha256((VENDOR / "axe.min.js").read_bytes()).hexdigest()
    assert axe_source().startswith("/*! axe v4.10.3")
    assert (VENDOR / "LICENSE").read_text(encoding="utf-8").startswith("Mozilla Public License, version 2.0")
    assert (VENDOR / "LICENSE-3RD-PARTY.txt").is_file()


def test_bundle_is_package_data() -> None:
    setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    assert setuptools["package-data"]["testzeus_hercules.quality"] == ["*.js", "vendor/axe/*"]


def test_quality_package_never_names_a_cdn() -> None:
    for path in (ROOT / "testzeus_hercules" / "quality").rglob("*"):
        if path.is_file() and path.suffix in (".py", ".js") and path.name != "axe.min.js":
            text = path.read_text(encoding="utf-8")
            assert not any(cdn in text for cdn in ("cdnjs", "unpkg", "jsdelivr")), path


@pytest.mark.parametrize(
    ("tags", "wcag", "level"),
    [
        (["cat.color", "wcag2aa", "wcag143", "TTv5"], "1.4.3", "AA"),
        (["wcag2a", "wcag412", "section508"], "4.1.2", "A"),
        (["wcag22aa", "wcag258"], "2.5.8", "AA"),
        (["wcag2aaa", "wcag146"], "1.4.6", "AAA"),
        (["wcag21aa", "wcag1412"], "1.4.12", "AA"),
        (["best-practice", "cat.semantics"], None, "none"),
    ],
)
def test_wcag_mapping(tags: list[str], wcag: str | None, level: str) -> None:
    assert wcag_of(tags) == wcag
    assert level_of(tags) == level


def test_default_tags_are_wcag_2x_a_and_aa() -> None:
    assert DEFAULT_TAGS == ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")


async def _serve_offline(context: Any, attempts: list[str]) -> None:
    """Serve fixture files for FIXTURE_HOST from disk; record and abort every other request."""

    async def handle(route: Any) -> None:
        url = route.request.url
        if url.startswith(FIXTURE_HOST):
            path = QUALITY / url[len(FIXTURE_HOST) :].split("?")[0]
            content_type = "image/svg+xml" if path.suffix == ".svg" else "text/html"
            await route.fulfill(status=200, body=path.read_bytes(), headers={"content-type": content_type})
            return
        attempts.append(url)
        await route.abort()

    await context.route("**/*", handle)


def test_axe_runs_offline_across_frames_and_shadow_dom(browser: BrowserHarness) -> None:  # noqa: F811
    attempts: list[str] = []
    requests: list[str] = []

    async def run() -> tuple[Any, Any]:
        context = await browser.browser.new_context(viewport={"width": 1280, "height": 800})  # type: ignore[attr-defined]
        try:
            await _serve_offline(context, attempts)
            page = await context.new_page()
            page.on("request", lambda request: requests.append(request.url))
            await page.goto(FIXTURE_HOST + "a11y.html")
            await page.wait_for_load_state("load")
            snapshot = await take_snapshot(page, seq=1)
            return await run_axe(page, snapshot=snapshot), snapshot
        finally:
            await context.close()

    result, snapshot = browser.run(run())
    assert attempts == []
    assert all(url.startswith((FIXTURE_HOST, "data:")) for url in requests)
    assert result.version == "4.10.3"
    assert result.scanned == ["top", "Inner form"]
    assert result.not_scanned == ["data:text/html,%3Cbutton%3E%3C/button%3E"]
    found = {(f.rule, f.selector, tuple(f.frame_path)) for f in result.findings}
    assert found == {
        ("button-name", "#dismiss", ()),
        ("button-name", "#host >> :scope > button", ()),
        ("color-contrast", "#fine-print", ()),
        ("color-contrast", "#subscribe", ()),
        ("image-alt", "#team", ()),
        ("target-size", "#help", ()),
        ("color-contrast", "#save", ("#inner",)),
    }
    by_key = {(f.rule, f.selector): f for f in result.findings}
    contrast = by_key[("color-contrast", "#subscribe")]
    assert (contrast.source, contrast.wcag, contrast.level, contrast.impact) == ("axe", "1.4.3", "AA", "serious")
    assert contrast.measured["data"]["contrastRatio"] == 3.29 and contrast.measured["data"]["bgColor"] == "#4a90e2"
    shadow = by_key[("button-name", "#host >> :scope > button")]
    assert shadow.element_md is not None and snapshot.element(shadow.element_md).role == "button"
    assert by_key[("color-contrast", "#save")].frame == "Inner form"


def test_axe_falls_back_when_a_policy_blocks_script_tags(browser: BrowserHarness) -> None:  # noqa: F811
    async def run() -> Any:
        context = await browser.browser.new_context()  # type: ignore[attr-defined]
        try:
            page = await context.new_page()
            await page.goto((QUALITY / "csp.html").as_uri())
            return await run_axe(page)
        finally:
            await context.close()

    result = browser.run(run())
    assert [(f.rule, f.selector) for f in result.findings] == [("button-name", "#blank")]
