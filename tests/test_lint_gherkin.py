"""Offline tests for scripts/lint_gherkin.py.

Runs on tmp feature files. The optional gherkin-official parser is exercised
with a fake module injected into sys.modules (restored by monkeypatch), so the
tests never depend on the real package being installed.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(script_name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{script_name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint_gherkin = _load_script("lint_gherkin", "lint_gherkin_test")

GOOD_FEATURE = """Feature: Checkout

  Scenario: CHECKOUT-01 pay by card
    Given a cart with one item
    When the pay button is clicked
    Then the order is confirmed
"""


def _write_feature(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _messages(result, severity: str) -> list[str]:
    return [finding.message for finding in result.findings if finding.severity == severity]


def test_good_file_has_no_findings(tmp_path: Path) -> None:
    path = _write_feature(tmp_path / "good.feature", GOOD_FEATURE)
    result = lint_gherkin.lint_paths([path])
    assert result.files_checked == 1
    assert result.findings == []


def test_empty_step_and_stepless_scenario_flagged(tmp_path: Path) -> None:
    text = """Feature: Broken

  Scenario: B-01 empty step
    Given a thing
    When
    Then done

  Scenario: B-02 no steps at all
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "broken.feature", text)])
    errors = _messages(result, "error")
    assert any("empty step" in message for message in errors)
    assert any("no steps" in message for message in errors)


def test_missing_given_when_then_is_warning_only(tmp_path: Path) -> None:
    text = """Feature: Heuristic

  Scenario: H-01 only and-steps
    And something happens
    And more of it
"""
    path = _write_feature(tmp_path / "heuristic.feature", text)
    result = lint_gherkin.lint_paths([path])
    assert result.errors == []
    assert any("Given/When/Then" in message for message in _messages(result, "warning"))
    assert lint_gherkin.main([str(path)]) == 0
    assert lint_gherkin.main(["--strict", str(path)]) == 1


def test_duplicate_scenario_titles_flagged(tmp_path: Path) -> None:
    text = """Feature: Dup

  Scenario: SAME-01 first
    Given a

  Scenario: SAME-01 first
    Given b
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "dup.feature", text)])
    assert any("duplicate scenario title" in message for message in _messages(result, "error"))


def test_missing_feature_line_and_empty_title_flagged(tmp_path: Path) -> None:
    text = """Scenario: orphaned
    Given a

  Scenario:
    Given b
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "orphan.feature", text)])
    errors = _messages(result, "error")
    assert any("no 'Feature:' line" in message for message in errors)
    assert any("no title" in message for message in errors)


def test_blocked_marker_lookalike_flagged(tmp_path: Path) -> None:
    text = """Feature: Markers

  @block:x
  Scenario: M-01 lookalike
    Given a

  @blocked:fixture
  Scenario: M-02 missing reason
    Given a
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "markers.feature", text)])
    errors = _messages(result, "error")
    assert any("malformed blocked tag" in message for message in errors)
    assert any("without a '# blocked:" in message for message in errors)


def test_valid_blocked_marker_not_flagged(tmp_path: Path) -> None:
    text = """Feature: Markers ok

  @blocked:env
  # blocked: staging is down
  Scenario: M-03 properly blocked
    Given a
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "ok.feature", text)])
    assert result.findings == []


def test_detached_blocked_pair_flagged(tmp_path: Path) -> None:
    # A pair separated from its scenario by a blank line is detached: it is emitted
    # verbatim by the assembler and the supposedly blocked case runs anyway.
    text = """Feature: Detached

  Scenario: D-01 first
    Given a

  @blocked:fixture
  # blocked: pool is down

  Scenario: D-02 second
    Given a
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "detached.feature", text)])
    errors = _messages(result, "error")
    assert any("detached" in message.lower() for message in errors)


def test_plain_multi_tag_line_not_flagged(tmp_path: Path) -> None:
    # A blocked-lookalike-free multi-tag line is legal Gherkin; only blocked
    # lookalikes are lint errors (mirroring the assembler).
    text = """Feature: Multi tags

  @ui @desktop
  Scenario: MT-01 tagged twice
    Given a thing
    When it happens
    Then it is done
"""
    result = lint_gherkin.lint_paths([_write_feature(tmp_path / "multi.feature", text)])
    assert _messages(result, "error") == []


def test_directory_input_is_scanned(tmp_path: Path) -> None:
    root = tmp_path / "cases"
    _write_feature(root / "a" / "one.feature", GOOD_FEATURE)
    _write_feature(root / "b" / "two.feature", GOOD_FEATURE)
    result = lint_gherkin.lint_paths([root])
    assert result.files_checked == 2


def test_manifest_cross_check_warns_on_unmatched_entry(tmp_path: Path) -> None:
    feature = _write_feature(tmp_path / "lib.feature", GOOD_FEATURE)
    manifest = tmp_path / "regression.txt"
    manifest.write_text("web/smoke :: CHECKOUT-01\nweb/smoke :: RENAMED-AWAY-99\n", encoding="utf-8")
    result = lint_gherkin.lint_paths([feature], manifest=manifest)
    assert result.manifest_entries == 2
    warnings = [finding for finding in result.warnings if finding.path == manifest]
    assert len(warnings) == 1
    assert "RENAMED-AWAY-99" in warnings[0].message
    assert result.errors == []


def test_syntax_fallback_wiring(monkeypatch, tmp_path: Path) -> None:
    path = _write_feature(tmp_path / "good.feature", GOOD_FEATURE)
    monkeypatch.setattr(lint_gherkin, "_gherkin_syntax_errors", lambda text: None)
    result = lint_gherkin.lint_paths([path])
    assert result.syntax_checker == "regex-fallback"
    assert result.findings == []

    broken = _write_feature(tmp_path / "broken.feature", "Feature: B\n\n  Scenario: no steps\n")
    result2 = lint_gherkin.lint_paths([broken])
    assert result2.syntax_checker == "regex-fallback"
    assert result2.errors  # structural checks still run without the parser lib


def test_gherkin_lib_present_path_via_fake_module(monkeypatch, tmp_path: Path) -> None:
    gherkin_pkg = types.ModuleType("gherkin")
    parser_mod = types.ModuleType("gherkin.parser")

    class _FakeParser:
        def parse(self, text: str) -> None:
            if "BROKEN" in text:
                raise RuntimeError("MockCompositeParserException: got stuck")

    parser_mod.Parser = _FakeParser
    monkeypatch.setitem(sys.modules, "gherkin", gherkin_pkg)
    monkeypatch.setitem(sys.modules, "gherkin.parser", parser_mod)

    assert lint_gherkin._gherkin_syntax_errors("Feature: fine\n") == []
    errors = lint_gherkin._gherkin_syntax_errors("BROKEN content")
    assert errors == ["MockCompositeParserException: got stuck"]

    path = _write_feature(tmp_path / "bad.feature", "Feature: B\nBROKEN tail\n")
    result = lint_gherkin.lint_paths([path])
    assert result.syntax_checker == "gherkin-official"
    assert any("syntax:" in message for message in _messages(result, "error"))


def test_syntax_probe_matches_environment() -> None:
    try:
        import gherkin.parser  # noqa: F401
    except ImportError:
        installed = False
    else:
        installed = True
    assert (lint_gherkin._gherkin_syntax_errors("Feature: probe\n") is not None) == installed


def test_main_exit_codes(tmp_path: Path) -> None:
    good = _write_feature(tmp_path / "good.feature", GOOD_FEATURE)
    bad = _write_feature(tmp_path / "bad.feature", "Feature: B\n\n  Scenario: no steps\n")
    assert lint_gherkin.main([str(good), "--manifest", str(tmp_path / "absent.txt")]) == 0
    assert lint_gherkin.main([str(bad), "--manifest", str(tmp_path / "absent.txt")]) == 0  # informational by default
    assert lint_gherkin.main(["--strict", str(bad), "--manifest", str(tmp_path / "absent.txt")]) == 1
