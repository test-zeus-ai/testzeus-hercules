"""Tests for the shared JSONPath helper (utils/jsonpath_helper)."""

import importlib.util

import pytest
from testzeus_hercules.utils.jsonpath_helper import (
    JSONPATH_NG_INSTALL_HINT,
    closest_parent_keys,
    find_json_path,
    path_leaf_name,
)

HAS_JSONPATH_NG = importlib.util.find_spec("jsonpath_ng") is not None

DOC = {
    "body": {
        "items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        "total": 2,
    },
    "status": "ok",
}

requires_jsonpath_ng = pytest.mark.skipif(not HAS_JSONPATH_NG, reason="jsonpath_ng (api extra) is not installed")


@requires_jsonpath_ng
def test_find_json_path_returns_matched_values() -> None:
    assert find_json_path(DOC, "$.status") == ["ok"]
    assert find_json_path(DOC, "$.body.items[*].id") == [1, 2]


@requires_jsonpath_ng
def test_find_json_path_no_match_returns_empty_list() -> None:
    assert find_json_path(DOC, "$.body.missing") == []


@requires_jsonpath_ng
def test_find_json_path_invalid_expression_raises() -> None:
    with pytest.raises(Exception):
        find_json_path(DOC, "$.body[")


@requires_jsonpath_ng
def test_closest_parent_keys_names_keys_at_closest_parent() -> None:
    info = closest_parent_keys(DOC, "$.body.items[0].nope")
    assert info == {"location": "$.body.items[0]", "keys": ["id", "name"]}


@requires_jsonpath_ng
def test_closest_parent_keys_handles_list_parent() -> None:
    info = closest_parent_keys(DOC, "$.body.items[99]")
    assert info is not None
    assert info["location"] == "$.body.items"
    assert info["keys"] == ["[0]", "[1]"]


@requires_jsonpath_ng
def test_closest_parent_keys_none_when_nothing_resolves() -> None:
    assert closest_parent_keys(DOC, "$.nowhere.deeper.still") is None


@requires_jsonpath_ng
def test_path_leaf_name_returns_named_field_only() -> None:
    assert path_leaf_name("$.body.total") == "total"
    assert path_leaf_name("$.body.items[0]") is None
    assert path_leaf_name("$.body.items[*]") is None


@pytest.mark.skipif(HAS_JSONPATH_NG, reason="only observable when jsonpath_ng is absent")
def test_missing_library_raises_import_error_with_install_hint(monkeypatch) -> None:
    # The helper must never break the package import; it defers the failure
    # to call time and names the pip extra that provides jsonpath_ng.
    import sys

    monkeypatch.setitem(sys.modules, "jsonpath_ng", None)
    monkeypatch.setitem(sys.modules, "jsonpath_ng.ext", None)
    # find_json_path imports fresh from jsonpath_ng.ext; with the modules
    # masked the import fails inside the call.
    with pytest.raises(ImportError) as excinfo:
        find_json_path(DOC, "$.status")
    assert "testzeus-hercules[api]" in str(excinfo.value)
    assert JSONPATH_NG_INSTALL_HINT in str(excinfo.value)


@pytest.mark.parametrize(
    ("path", "singular"),
    [
        ("$", True),
        ("$.a", True),
        ("$.a[0].b", True),
        ("$.a[*]", False),
        ("$.a.*", False),
        ("$.a[1:3]", False),
        ("$.a[?(@ > 1)]", False),
        ("$..a", False),
        ("$['a','b']", False),
        ("$.a[*].b", False),
    ],
)
def test_is_singular_path(path, singular) -> None:
    pytest.importorskip("jsonpath_ng")
    from testzeus_hercules.utils.jsonpath_helper import is_singular_path

    assert is_singular_path(path) is singular
