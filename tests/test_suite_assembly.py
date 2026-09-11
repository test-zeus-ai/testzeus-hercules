"""Offline tests for scripts/assemble_version.py.

The script is loaded by file path (importlib) so no package install is needed.
All cases build a tmp case library and assert on the emitted files; no runner
invocation, no network, no LLM.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_script(script_name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{script_name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assemble_version = _load_script("assemble_version", "assemble_version_test")
AssemblyError = assemble_version.AssemblyError

FEATURE_AUTH_V1 = """Feature: Auth regression header v1

  Background:
    Given the api base url is configured

  Scenario: AUTH-LOGIN-01 valid login
    Given a registered user
    When the login endpoint is called
    Then the status is 200
"""

FEATURE_AUTH_V2 = """Feature: Auth regression header v2

  Scenario: AUTH-TOKEN-02 refresh token
    Given a valid refresh token
    When the token endpoint is called
    Then a new access token is returned
"""

FEATURE_WEB_SMOKE = """Feature: Web smoke

  @smoke
  # checked during onboarding
  Scenario: WEB-SMOKE-01 open home page
    Given the browser is open
    When the home page loads
    Then the title is visible
"""

FEATURE_PAYMENTS = """Feature: Payments

  Scenario: PAY-CHARGE-01 charge a card
    Given a card on file
    When the charge endpoint is called
    Then the charge is approved

  @blocked:fixture
  # blocked: test card pool is not provisioned in this environment
  Scenario: PAY-CHARGE-02 charge a saved card
    Given a saved card
    When the charge endpoint is called
    Then the charge is approved
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "cases"
    _write(lib / "api" / "auth" / "1.0.feature", FEATURE_AUTH_V1)
    _write(lib / "api" / "auth" / "2.0.feature", FEATURE_AUTH_V2)
    _write(lib / "api" / "auth" / "test_data" / "users.json", '{"alice": "pw"}')
    _write(lib / "api" / "payments" / "1.0.feature", FEATURE_PAYMENTS)
    _write(lib / "web" / "smoke" / "v10.0.feature", FEATURE_WEB_SMOKE)
    return lib


def test_parse_version_orders_10_after_9() -> None:
    assert assemble_version.parse_version("10.0") > assemble_version.parse_version("9.0")
    assert assemble_version.parse_version("v2.0") == (2, 0)
    assert assemble_version.parse_version("1.2.3") == (1, 2, 3)
    with pytest.raises(AssemblyError):
        assemble_version.parse_version("ten")


def test_version_mode_assembles_only_requested_version(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, version="2.0")
    assert result.target == "2.0"
    feature = run_root / "2.0" / "tests" / "api-auth" / "input" / "api-auth.feature"
    assert feature.is_file()
    text = feature.read_text(encoding="utf-8")
    assert "AUTH-TOKEN-02" in text
    assert "AUTH-LOGIN-01" not in text  # no accumulation across versions
    assert "Feature: Auth regression header v2" in text
    assert (run_root / "2.0" / "tests" / "api-auth" / "test_data" / "users.json").is_file()


def test_version_filename_leading_v_is_recognized(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, version="v10.0", types=("web",))
    assert [f"{s.case_type}-{s.module}" for s in result.suites] == ["web-smoke"]
    assert "WEB-SMOKE-01" in (run_root / "10.0" / "tests" / "web-smoke" / "input" / "web-smoke.feature").read_text(encoding="utf-8")


def test_regression_merges_header_from_first_file(library: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "regression.txt"
    _write(manifest, "# regression pick\napi/auth :: AUTH-LOGIN-01\napi/auth :: AUTH-TOKEN-02\n")
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, manifest=manifest)
    assert result.target == "regression"
    feature = run_root / "regression" / "tests" / "api-auth" / "input" / "api-auth.feature"
    text = feature.read_text(encoding="utf-8")
    assert "Feature: Auth regression header v1" in text  # header from the first contributing file
    assert "Auth regression header v2" not in text
    assert text.index("AUTH-LOGIN-01") < text.index("AUTH-TOKEN-02")  # deterministic order
    assert "Given the api base url is configured" in text  # Background kept


def test_tag_and_comment_run_is_captured_above_scenario(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    assemble_version.assemble(library, run_root, version="10.0", types=("web",))
    text = (run_root / "10.0" / "tests" / "web-smoke" / "input" / "web-smoke.feature").read_text(encoding="utf-8")
    stripped = "\n".join(line.strip() for line in text.splitlines())
    assert "@smoke\n# checked during onboarding\nScenario: WEB-SMOKE-01" in stripped


def test_blocked_case_excluded_and_blocked_json_written(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, version="1.0")
    payments = (run_root / "1.0" / "tests" / "api-payments" / "input" / "api-payments.feature").read_text(encoding="utf-8")
    assert "PAY-CHARGE-01" in payments
    assert "PAY-CHARGE-02" not in payments
    blocked_path = run_root / "1.0" / "blocked.json"
    payload = json.loads(blocked_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "case-library-blocked/v1"
    assert payload["count"] == 1
    assert payload["excluded"][0]["scenario"] == "PAY-CHARGE-02 charge a saved card"
    assert payload["excluded"][0]["category"] == "fixture"
    assert "card pool" in payload["excluded"][0]["reason"]
    assert result.excluded == payload["excluded"]


def test_blocked_json_written_even_when_nothing_is_blocked(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    assemble_version.assemble(library, run_root, version="2.0")
    payload = json.loads((run_root / "2.0" / "blocked.json").read_text(encoding="utf-8"))
    assert payload["count"] == 0
    assert payload["excluded"] == []


def test_blocked_markers_are_stripped_from_assembled_output(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    assemble_version.assemble(library, run_root, version="1.0", include_blocked=True)
    payments = (run_root / "1.0" / "tests" / "api-payments" / "input" / "api-payments.feature").read_text(encoding="utf-8")
    stripped = "\n".join(line.strip() for line in payments.splitlines())
    assert "@blocked:" not in stripped
    assert "# blocked:" not in stripped
    assert "PAY-CHARGE-02" in payments  # included by override, marker still stripped
    # the marker run must not leak into the tail of the preceding block either
    charge01_tail = stripped.split("Scenario: PAY-CHARGE-01")[1].split("Scenario: PAY-CHARGE-02")[0]
    assert "@blocked" not in charge01_tail


def test_include_blocked_override(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, version="1.0", include_blocked=True)
    assert result.excluded == []
    payload = json.loads((run_root / "1.0" / "blocked.json").read_text(encoding="utf-8"))
    assert payload["count"] == 0 and payload["include_blocked"] is True


@pytest.mark.parametrize(
    "marker_text",
    [
        "@block:x\n# blocked: nope\nScenario: MALFORMED-01\n    Given a thing",
        "@blocked:fixture @wip\n# blocked: reason\nScenario: MALFORMED-02\n    Given a thing",
        "@blocked:fixture\nScenario: MALFORMED-03\n    Given a thing",
        "# blocked: reason without a tag\nScenario: MALFORMED-04\n    Given a thing",
        "Scenario: MALFORMED-05\n    # not executable until the fixture exists\n    Given a thing",
    ],
)
def test_malformed_marker_aborts_assembly(library: Path, tmp_path: Path, marker_text: str) -> None:
    _write(library / "api" / "auth" / "1.0.feature", FEATURE_AUTH_V1 + "\n" + marker_text + "\n")
    with pytest.raises(AssemblyError):
        assemble_version.assemble(library, tmp_path / "run", version="1.0")


def test_detached_blocked_pair_aborts_assembly(library: Path, tmp_path: Path) -> None:
    # The pair is separated from its scenario by a blank line: it lands in the previous
    # block's tail, is emitted verbatim, and the "blocked" case runs (blocked.json count 0).
    detached = (
        "Feature: Detached\n"
        "\n"
        "  Scenario: DET-01 first\n"
        "    Given a thing\n"
        "    When it happens\n"
        "    Then it is fine\n"
        "\n"
        "  @blocked:fixture\n"
        "  # blocked: pool is down\n"
        "\n"
        "  Scenario: DET-02 second\n"
        "    Given a thing\n"
    )
    _write(library / "api" / "auth" / "1.0.feature", detached)
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, tmp_path / "run", version="1.0")
    assert "1.0.feature" in str(excinfo.value)  # abort names the file (and line)


def test_detached_blocked_pair_in_header_aborts_assembly(library: Path, tmp_path: Path) -> None:
    detached = "Feature: Detached\n" "\n" "  @blocked:env\n" "  # blocked: environment is down\n" "\n" "  Scenario: DET-03 only scenario\n" "    Given a thing\n"
    _write(library / "api" / "auth" / "1.0.feature", detached)
    with pytest.raises(AssemblyError):
        assemble_version.assemble(library, tmp_path / "run", version="1.0")


def test_plain_multi_tag_line_is_an_ordinary_tag(library: Path, tmp_path: Path) -> None:
    # Gherkin-legal multi-tag lines without a blocked lookalike must not abort assembly;
    # only blocked-lookalike tag lines are malformed.
    feature = "Feature: Dash\n" "\n" "  @ui @desktop\n" "  Scenario: UI-01 open dashboard\n" "    Given the browser is open\n" "    When the dashboard loads\n" "    Then the widgets are visible\n"
    _write(library / "ui" / "dash" / "1.0.feature", feature)
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, version="1.0", types=("ui",))
    text = (run_root / "1.0" / "tests" / "ui-dash" / "input" / "ui-dash.feature").read_text(encoding="utf-8")
    assert result.suites[0].scenario_count == 1
    assert "@ui @desktop" in text  # ordinary tags kept verbatim, output shape stable


def test_types_with_path_components_abort(library: Path, tmp_path: Path) -> None:
    # "--types ../outside" must not read outside the library or materialize
    # "tests/../outside-auth" outside the tests/ root.
    escape = tmp_path / "outside"
    _write(escape / "auth" / "1.0.feature", FEATURE_AUTH_V1)
    run_root = tmp_path / "run"
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, run_root, version="1.0", types=("../outside",))
    assert "invalid case type" in str(excinfo.value)
    assert not run_root.exists()  # nothing materialized outside the library


@pytest.mark.parametrize("bad_type", ["/abs", ".hidden", "a/b", "..", "api/x"])
def test_invalid_type_names_abort(library: Path, tmp_path: Path, bad_type: str) -> None:
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, tmp_path / "run", version="1.0", types=(bad_type,))
    assert "invalid case type" in str(excinfo.value)


def test_materialize_containment_guard(tmp_path: Path) -> None:
    # Defense in depth behind --types validation: no materialized suite path may
    # escape <run_dir>/tests.
    block = assemble_version.ScenarioBlock(title="T-01 x", lines=["Scenario: T-01 x", "    Given a"], start_line=1, scenario_line=1)
    suites = [("../evil", "auth", ["Feature: Evil"], [block])]
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version._materialize(suites, tmp_path / "lib", tmp_path / "run" / "t", dry_run=False)
    assert "escapes" in str(excinfo.value)
    assert not (tmp_path / "run" / "t").exists()


def test_regression_merge_drops_duplicate_titles(library: Path, tmp_path: Path, capsys) -> None:
    # The same-titled scenario in two versions used to be merged twice -- a duplicate
    # title the project's own linter rejects.
    _write(library / "api" / "auth" / "2.0.feature", FEATURE_AUTH_V1)
    manifest = tmp_path / "regression.txt"
    _write(manifest, "api/auth :: AUTH-LOGIN-01\n")
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, manifest=manifest)
    text = (run_root / "regression" / "tests" / "api-auth" / "input" / "api-auth.feature").read_text(encoding="utf-8")
    assert text.count("Scenario: AUTH-LOGIN-01 valid login") == 1  # first occurrence kept
    assert sum(suite.scenario_count for suite in result.suites) == 1
    assert "dropped 1 duplicate scenario(s)" in capsys.readouterr().out


def test_regression_entry_matching_nothing_aborts(library: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "regression.txt"
    _write(manifest, "api/auth :: AUTH-RENAME-99\n")
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, tmp_path / "run", manifest=manifest)
    message = str(excinfo.value)
    assert "AUTH-RENAME-99" in message
    assert "matched no scenario" in message
    assert "AUTH-LOGIN-01" in message  # helpful hint: titles that do exist


def test_regression_entry_with_unknown_module_aborts_listing_existing(library: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "regression.txt"
    _write(manifest, "api/checkout :: SOME-CASE-01\n")
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, tmp_path / "run", manifest=manifest)
    message = str(excinfo.value)
    assert "unknown module 'api/checkout'" in message
    assert "api/auth" in message and "web/smoke" in message


def test_regression_entry_pointing_at_blocked_case_aborts(library: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "regression.txt"
    _write(manifest, "api/payments :: PAY-CHARGE-02\n")
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, tmp_path / "run", manifest=manifest)
    assert "blocked" in str(excinfo.value)


def test_regression_blocked_entry_aborts_even_with_include_blocked(library: Path, tmp_path: Path) -> None:
    # Deliberate policy: a regression entry that only matches blocked cases is exactly
    # the always-red noise the convention exists to remove; --include-blocked does not rescue it.
    manifest = tmp_path / "regression.txt"
    _write(manifest, "api/payments :: PAY-CHARGE-02\n")
    with pytest.raises(AssemblyError):
        assemble_version.assemble(library, tmp_path / "run", manifest=manifest, include_blocked=True)


def test_dry_run_prints_plan_and_writes_nothing(library: Path, tmp_path: Path, capsys) -> None:
    run_root = tmp_path / "run"
    result = assemble_version.assemble(library, run_root, version="1.0", dry_run=True)
    assert result.dry_run is True
    out = capsys.readouterr().out
    assert "(dry-run)" in out
    assert "suite api-auth" in out
    assert "PAY-CHARGE-02" in out  # excluded cases are printed
    assert not run_root.exists()


def test_version_without_any_case_file_aborts(library: Path, tmp_path: Path) -> None:
    with pytest.raises(AssemblyError) as excinfo:
        assemble_version.assemble(library, tmp_path / "run", version="99.0")
    assert "no case file for version 99.0" in str(excinfo.value)


def test_main_returns_2_on_aborted_assembly(library: Path, tmp_path: Path) -> None:
    assert assemble_version.main(["--version", "99.0", "--library", str(library), "--run-root", str(tmp_path / "run")]) == 2


def test_main_assembles_via_cli(library: Path, tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    assert assemble_version.main(["--version", "1.0", "--library", str(library), "--run-root", str(run_root)]) == 0
    assert (run_root / "1.0" / "tests" / "api-auth" / "input" / "api-auth.feature").is_file()
    assert (run_root / "1.0" / "blocked.json").is_file()
