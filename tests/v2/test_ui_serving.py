"""The daemon serves the built tester UI: sign-in first, SPA fallback, strict CSP, caching, confinement."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from testzeus_hercules.daemon.security import SESSION_COOKIE
from testzeus_hercules.ui.assets import UI_CSP, resolve_asset, sign_in_page
from testzeus_hercules.ui.brand import PAGE_CSP, PAGE_STYLE
from tests.v2.fixtures.daemon_support import TOKEN, client_for, make_config

INDEX = '<!doctype html><html><head><script type="module" crossorigin src="/assets/index-AbCd1234.js"></script></head><body><div id="root"></div></body></html>'
HTML = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture()
def ui_dir(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (root / "assets" / "index-AbCd1234.js").write_text("console.info('ui')", encoding="utf-8")
    (root / "assets" / "index-EfGh5678.css").write_text("body{}", encoding="utf-8")
    (root / "assets" / "font-Ij90Kl12.woff2").write_bytes(b"wOF2")
    (tmp_path / "secret.txt").write_text("outside the static directory", encoding="utf-8")
    return root


@pytest.fixture()
def config(tmp_path: Path, ui_dir: Path):  # type: ignore[no-untyped-def]
    return make_config(tmp_path, ui_dir=ui_dir)


def _signed_in(client) -> None:  # type: ignore[no-untyped-def]
    url = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"]
    response = client.get(url.replace("http://127.0.0.1:7373", ""), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_ui_needs_the_session_cookie_or_token(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        api_style = client.get("/")
        page = client.get("/runs/r_1", headers=HTML)
        asset = client.get("/assets/index-AbCd1234.js")
        client.cookies.set(SESSION_COOKIE, "made-up")
        forged = client.get("/", headers=HTML)
    assert api_style.status_code == 401
    assert api_style.headers["content-type"].startswith("application/json")
    assert page.status_code == 401
    assert page.headers["content-type"].startswith("text/html")
    assert "hercules ui" in page.text
    assert page.headers["content-security-policy"] == PAGE_CSP
    assert "Hercules" in page.text and 'aria-label="TestZeus"' in page.text
    assert 'id="root"' not in page.text  # not the SPA index
    assert asset.status_code == 401
    assert forged.status_code == 401
    for response in (api_style, page, asset, forged):
        assert TOKEN not in response.text


def test_login_link_signs_in_and_serves_the_app(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        _signed_in(client)
        response = client.get("/", headers=HTML)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


def test_used_login_link_explains_how_to_get_a_new_one(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config, auth=False) as client:
        url = client.post("/api/login-url", headers={"Authorization": f"Bearer {TOKEN}"}).json()["url"].replace("http://127.0.0.1:7373", "")
        assert client.get(url, follow_redirects=False).status_code == 303
        client.cookies.clear()
        again = client.get(url, headers=HTML, follow_redirects=False)
    assert again.status_code == 401
    assert "already used or has expired" in again.text


def test_index_has_a_strict_csp_without_remote_origins_or_inline_script(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get("/")
    csp = response.headers["content-security-policy"]
    assert csp == UI_CSP
    directives = dict(part.strip().split(" ", 1) for part in csp.split(";"))
    assert directives["script-src"] == "'self'"
    assert directives["default-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "http" not in csp and "*" not in csp and "data:" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("route", ["/runs/r_20261026_101500_abc123", "/cases", "/cases/login.feature", "/environments", "/models", "/some/deep/client/route"])
def test_client_routes_fall_back_to_index(config, route: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get(route)
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-security-policy"] == UI_CSP


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        ("/assets/index-AbCd1234.js", "text/javascript"),
        ("/assets/index-EfGh5678.css", "text/css"),
        ("/assets/font-Ij90Kl12.woff2", "font/woff2"),
    ],
)
def test_hashed_assets_are_cached_forever_with_their_type(config, path: str, media_type: str) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_unhashed_root_files_revalidate(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "no-cache"


def test_missing_files_are_404_not_the_app(config) -> None:  # type: ignore[no-untyped-def]
    with client_for(config) as client:
        missing_asset = client.get("/assets/gone-12345678.js")
        missing_file = client.get("/robots.txt")
        unknown_api = client.get("/api/nothing-here")
    assert missing_asset.status_code == 404
    assert missing_file.status_code == 404
    assert unknown_api.status_code == 404
    assert unknown_api.json() == {"error": "not found"}
    for response in (missing_asset, missing_file):
        assert INDEX not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/cases/../../secret.txt",
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/..%5csecret.txt",
        "/.hidden.js",
        "/assets/link.js",
        "/%00.js",
    ],
)
def test_static_paths_are_confined(config, ui_dir: Path, path: str) -> None:  # type: ignore[no-untyped-def]
    os.symlink(ui_dir.parent / "secret.txt", ui_dir / "assets" / "link.js")
    (ui_dir / ".hidden.js").write_text("hidden", encoding="utf-8")
    with client_for(config) as client:
        response = client.get(path)
    assert "outside the static directory" not in response.text
    assert response.text != "hidden"
    assert response.status_code in (200, 404)
    if response.status_code == 200:  # a client route: only ever the app shell
        assert response.text == INDEX


def test_resolve_asset_rejects_unsafe_and_unknown_files(ui_dir: Path) -> None:
    (ui_dir / "notes.exe").write_bytes(b"MZ")
    os.symlink(ui_dir / "assets", ui_dir / "linked")
    assert resolve_asset(ui_dir, "assets/index-AbCd1234.js") is not None
    for bad in ("../secret.txt", "assets/../index.html", "notes.exe", "linked/index-AbCd1234.js", "", "/index.html", "assets\\index-AbCd1234.js", "assets"):
        assert resolve_asset(ui_dir, bad) is None, bad


def test_missing_build_shows_how_to_build_the_ui(tmp_path: Path) -> None:
    config = make_config(tmp_path, ui_dir=tmp_path / "not-built")
    with client_for(config) as client:
        root = client.get("/")
        route = client.get("/runs/r_1")
        health = client.get("/api/health")
    for response in (root, route):
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("text/html")
        assert "npm ci" in response.text and "npm run build" in response.text
        assert response.headers["content-security-policy"] == PAGE_CSP
    assert health.status_code == 200


def test_packaged_ui_directory_is_package_data() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    setuptools = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    patterns = setuptools["package-data"]["testzeus_hercules.ui"]
    assert "static/*" in patterns and "static/assets/*" in patterns
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "testzeus_hercules/ui/static/" in ignored


def test_daemon_pages_allow_only_their_own_hashed_stylesheet() -> None:
    import base64
    import hashlib
    import re

    page = sign_in_page(expired=True)
    styles = re.findall(r"<style>(.*?)</style>", page, flags=re.S)
    assert styles == [PAGE_STYLE]
    digest = base64.b64encode(hashlib.sha256(styles[0].encode()).digest()).decode()
    assert PAGE_CSP == f"default-src 'none'; style-src 'sha256-{digest}'"
    assert "<script" not in page and "<img" not in page and "url(" not in PAGE_STYLE
    assert 'aria-label="TestZeus"' in page and "<title>Hercules: sign in</title>" in page
