"""Offline tests for the SSRF-guarded fetcher and filesystem root confinement."""

import httpx
import pytest
from testzeus_hercules.utils.path_guard import ForbiddenFileError, PathOutsideRootsError, check_prd_document, is_sensitive_name, resolve_within
from testzeus_hercules.utils.url_guard import UnsafeURLError, fetch_text, validate_url

PUBLIC = "93.184.216.34"


def _resolver(mapping):
    def resolve(host, port):
        if host not in mapping:
            raise OSError("no such host")
        return mapping[host]

    return resolve


# ── validate_url ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://10.1.2.3/",
        "http://192.168.0.1/",
        "http://172.16.5.5/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://[::ffff:127.0.0.1]/",
        "http://user:pw@example.com/",
    ],
)
def test_validate_url_blocks(url) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(url, resolver=_resolver({"localhost": ["127.0.0.1"], "example.com": [PUBLIC]}))


def test_validate_url_blocks_hostname_resolving_private() -> None:
    with pytest.raises(UnsafeURLError, match="non-public"):
        validate_url("http://evil.example/", resolver=_resolver({"evil.example": [PUBLIC, "10.0.0.1"]}))


def test_validate_url_unresolvable_host() -> None:
    with pytest.raises(UnsafeURLError, match="resolve"):
        validate_url("http://nope.invalid/", resolver=_resolver({}))


def test_validate_url_allows_public_and_pins_ip() -> None:
    assert validate_url("https://example.com/p", resolver=_resolver({"example.com": [PUBLIC]})) == ("https", "example.com", 443, PUBLIC)


def test_validate_url_allow_private_opt_in() -> None:
    assert validate_url("http://127.0.0.1:9/", allow_private=True)[3] == "127.0.0.1"


# ── fetch_text ────────────────────────────────────────────────────────────────


def test_fetch_text_pins_connection_to_validated_ip_and_sends_host() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["header"] = request.headers["host"]
        return httpx.Response(200, text="hello")

    result = fetch_text("http://example.com/prd", resolver=_resolver({"example.com": [PUBLIC]}), transport=httpx.MockTransport(handler))
    assert result.text == "hello"
    assert seen == {"host": PUBLIC, "header": "example.com"}


def test_fetch_text_revalidates_every_redirect_hop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        raise AssertionError("must not connect to the metadata address")

    with pytest.raises(UnsafeURLError, match="non-public"):
        fetch_text("http://example.com/", resolver=_resolver({"example.com": [PUBLIC]}), transport=httpx.MockTransport(handler))


def test_fetch_text_follows_safe_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "a.example":
            return httpx.Response(301, headers={"location": "https://b.example/final"})
        return httpx.Response(200, text="final")

    result = fetch_text("http://a.example/", resolver=_resolver({"a.example": [PUBLIC], "b.example": ["93.184.216.35"]}), transport=httpx.MockTransport(handler))
    assert result.text == "final" and result.url == "https://b.example/final"


def test_fetch_text_redirect_loop_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://a.example/again"})

    with pytest.raises(UnsafeURLError, match="redirects"):
        fetch_text("http://a.example/", max_redirects=3, resolver=_resolver({"a.example": [PUBLIC]}), transport=httpx.MockTransport(handler))


def test_fetch_text_size_limit() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(UnsafeURLError, match="larger"):
        fetch_text("http://a.example/", max_bytes=1000, resolver=_resolver({"a.example": [PUBLIC]}), transport=transport)


def test_fetch_text_http_error_propagates() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="nope"))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_text("http://a.example/", resolver=_resolver({"a.example": [PUBLIC]}), transport=transport)


# ── resolve_within ────────────────────────────────────────────────────────────


def test_resolve_within_accepts_inside_and_rejects_outside(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "prd.md"
    inside.write_text("x")
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    assert resolve_within(str(inside), [str(root)]) == str(inside.resolve())
    with pytest.raises(PathOutsideRootsError):
        resolve_within(str(outside), [str(root)])
    with pytest.raises(PathOutsideRootsError):
        resolve_within(str(root / ".." / "secret.txt"), [str(root)])


def test_resolve_within_rejects_symlink_escape(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(PathOutsideRootsError):
        resolve_within(str(link), [str(root)])


def test_resolve_within_sibling_prefix_not_confused(tmp_path) -> None:
    root = tmp_path / "proj"
    sibling = tmp_path / "proj-evil"
    root.mkdir()
    sibling.mkdir()
    with pytest.raises(PathOutsideRootsError):
        resolve_within(str(sibling), [str(root)])


def test_resolve_within_no_roots_or_empty_path() -> None:
    with pytest.raises(PathOutsideRootsError):
        resolve_within("/tmp", [])
    with pytest.raises(PathOutsideRootsError):
        resolve_within("", ["/tmp"])


def test_resolve_within_ignores_filesystem_root_and_home_as_roots(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    doc = home / "notes" / "prd.md"
    doc.parent.mkdir()
    doc.write_text("x")
    monkeypatch.setenv("HOME", str(home))
    # "/" and $HOME would allow everything: both are dropped, so nothing resolves.
    for broad in ("/", str(home), "~"):
        with pytest.raises(PathOutsideRootsError):
            resolve_within(str(doc), [broad])
    # ...but a specific directory (even beneath $HOME) still works, alongside the broad ones.
    assert resolve_within(str(doc), ["/", str(home), str(doc.parent)]) == str(doc.resolve())


@pytest.mark.parametrize("name", ["prd.md", "spec.markdown", "notes.txt", "design.rst", "req.pdf", "brief.DOCX"])
def test_check_prd_document_allows_documents(tmp_path, name) -> None:
    doc = tmp_path / name
    doc.write_text("x")
    check_prd_document(str(doc))


@pytest.mark.parametrize(
    "name",
    [
        "prd.html",
        "config.json",
        "script.py",
        "noext",
        ".hidden.md",
        ".env",
        "agents_llm_config.json",
        "agents_llm_config-example.json.txt",
        "prod.env",
        "tls.pem",
        "server.key",
        "sessions.json",
        "hercules_history.db",
        "hercules_history.db-wal",
    ],
)
def test_check_prd_document_rejects_other_types_and_sensitive_names(tmp_path, name) -> None:
    doc = tmp_path / name
    doc.write_text("x")
    with pytest.raises(ForbiddenFileError):
        check_prd_document(str(doc))


def test_check_prd_document_rejects_symlink_to_sensitive_target(tmp_path) -> None:
    target = tmp_path / ".env"
    target.write_text("SECRET=1")
    link = tmp_path / "prd.md"
    link.symlink_to(target)
    with pytest.raises(ForbiddenFileError):
        check_prd_document(str(link))


def test_is_sensitive_name_matches_dashboard_artifacts() -> None:
    for name in ("hercules_history.db", "hercules_history.db-shm", "selector_store.json", "sessions.json", ".env.local", "x.sqlite3", "a.db", "/tmp/dir/.secret"):
        assert is_sensitive_name(name), name
    for name in ("report.html", "results.json", "run.md", "shot.png", ""):
        assert not is_sensitive_name(name), name
