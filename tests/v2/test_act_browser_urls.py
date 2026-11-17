"""Which URLs a step may open: host:port shorthands, about:blank, and file URLs confined to the project."""

from __future__ import annotations

from pathlib import Path

import pytest

from testzeus_hercules.act.browser import InvalidURLError, resolve_url


@pytest.mark.parametrize(
    ("given", "opened"),
    [
        ("localhost:3000/login", "http://localhost:3000/login"),
        ("127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("[::1]:8080/x?y=1", "http://[::1]:8080/x?y=1"),
        ("shop.test:8443/cart", "http://shop.test:8443/cart"),
    ],
)
def test_a_host_and_port_without_a_scheme_opens_as_http(given: str, opened: str) -> None:
    assert resolve_url(given, "http://127.0.0.1:9000/app/") == opened
    assert resolve_url(given, None) == opened


def test_about_blank_is_allowed() -> None:
    assert resolve_url("about:blank", None) == "about:blank"
    with pytest.raises(InvalidURLError):
        resolve_url("about:config", None)


def test_a_name_with_a_number_is_not_taken_for_a_host() -> None:
    with pytest.raises(InvalidURLError):
        resolve_url("mailto:123", None)


def test_file_urls_are_refused_without_a_project_folder() -> None:
    with pytest.raises(InvalidURLError, match="project"):
        resolve_url("file:///etc/passwd", None, file_roots=())
    with pytest.raises(InvalidURLError, match="project"):
        resolve_url("file:///etc/passwd", "http://127.0.0.1:8000/", file_roots=())


def test_by_default_file_urls_are_confined_to_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "page.html").write_text("x")
    monkeypatch.chdir(tmp_path)

    assert resolve_url((tmp_path / "page.html").as_uri(), None) == (tmp_path / "page.html").as_uri()
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url("file:///etc/passwd", None)


def test_file_urls_inside_the_project_open_and_outside_are_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "site").mkdir(parents=True)
    page = project / "site" / "index.html"
    page.write_text("<p>hi</p>")
    (tmp_path / "outside.html").write_text("<p>no</p>")

    assert resolve_url(page.as_uri(), None, file_roots=[project]) == page.as_uri()
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url((tmp_path / "outside.html").as_uri(), None, file_roots=[project])
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url("file:///etc/passwd", None, file_roots=[project])


def test_a_symlink_out_of_the_project_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "secret.html").write_text("x")
    (project / "link.html").symlink_to(tmp_path / "secret.html")

    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url((project / "link.html").as_uri(), None, file_roots=[project])


def test_a_file_base_url_allows_its_own_folder_only(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("a")
    (site / "next.html").write_text("b")
    base = (site / "index.html").as_uri()

    assert resolve_url(base, base) == base
    assert resolve_url("next.html", base) == (site / "next.html").as_uri()
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url("../../../../../../etc/passwd", base)
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url("../" * 64 + "etc/passwd", base, file_roots=[Path.cwd(), site])
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url("file:etc/passwd", base)
    with pytest.raises(InvalidURLError, match="outside"):
        resolve_url("file:///etc/passwd", base)
