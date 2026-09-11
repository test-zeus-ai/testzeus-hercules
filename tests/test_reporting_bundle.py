"""Offline tests for testzeus_hercules.reporting.bundle.

Builds a synthetic merged JUnit XML and a fake evidence tree, then verifies
parsing, per-failure bundle creation and bundle.md content. No network, no LLM.
"""

import asyncio
import os
import unicodedata
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from testzeus_hercules.reporting.bundle import (
    _escape_inline,
    create_failure_bundles,
    create_run_bundles,
    parse_junit_testcases,
    resolve_bundle_dir_names,
    sanitize_name,
)

PROOFS_PROPERTY_NAME = "Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs"

PNG_HEADER = b"\x89PNG\r\n\x1a\n"
VIDEO_BYTES = b"FAKE-WEBM-VIDEO"
NETWORK_BYTES = b'{"requests": [{"url": "https://example.test/api", "status": 500}]}'
CONSOLE_BYTES = b'{"level": "error", "text": "TypeError: cannot read properties of undefined"}'
PLANNER_BYTES = b'{"thoughts": ["try selector #bikes", "selector did not match"]}'
INNER_THOUGHTS_BYTES = b'{"steps": [{"thought": "element not found"}]}'

FAIL_FIRST_BYTES = PNG_HEADER + b"FAIL-FIRST"
SHOT_A_BYTES = PNG_HEADER + b"SHOT-A"
SHOT_B_BYTES = PNG_HEADER + b"SHOT-B"
SHOT_C_BYTES = PNG_HEADER + b"SHOT-C"
FINAL_STATE_BYTES = PNG_HEADER + b"FINAL-STATE"

LONG_PROPERTY_VALUE = "x" * 300


def _touch(path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _make_evidence_tree(base) -> dict:
    """Create a fake proofs/evidence tree with controlled mtimes; returns the property paths."""
    proofs_dir = base / "proofs" / "stake_1" / "123"
    screenshots_dir = proofs_dir / "screenshots"
    videos_dir = proofs_dir / "videos"
    logs_dir = base / "logs" / "stake_1"
    planner_dir = base / "planner"
    for directory in (screenshots_dir, videos_dir, logs_dir, planner_dir):
        directory.mkdir(parents=True, exist_ok=True)

    base_ts = 1_700_000_000.0

    pngs = [
        ("fail_first.png", FAIL_FIRST_BYTES, base_ts),
        ("shot_a.png", SHOT_A_BYTES, base_ts + 10),
        ("shot_b.png", SHOT_B_BYTES, base_ts + 20),
        ("shot_c.png", SHOT_C_BYTES, base_ts + 30),
        ("final_state.png", FINAL_STATE_BYTES, base_ts + 40),
    ]
    for name, content, mtime in pngs:
        png_path = screenshots_dir / name
        png_path.write_bytes(content)
        _touch(png_path, mtime)

    video_path = videos_dir / "recording.webm"
    video_path.write_bytes(VIDEO_BYTES)
    _touch(video_path, base_ts)

    network_path = base / "network_logs.json"
    network_path.write_bytes(NETWORK_BYTES)
    _touch(network_path, base_ts)

    (proofs_dir / "console_logs.json").write_bytes(CONSOLE_BYTES)
    (logs_dir / "agent_inner_thoughts.json").write_bytes(INNER_THOUGHTS_BYTES)
    (planner_dir / "planner_thoughts.json").write_bytes(PLANNER_BYTES)

    # Fake Playwright trace: 2 html entries (one clearly larger) plus a non-html entry.
    trace_path = proofs_dir / "trace.zip"
    with zipfile.ZipFile(trace_path, "w") as archive:
        archive.writestr("data/index.html", "<html>" + "A" * 5000 + "</html>")
        archive.writestr("data/final.html", "<html>" + "B" * 100 + "</html>")
        archive.writestr("data/trace.css", "body { color: red; }")
    _touch(trace_path, base_ts)

    return {
        "proofs_dir": str(proofs_dir),
        "video": str(video_path),
        "network": str(network_path),
        "planner": str(planner_dir / "planner_thoughts.json"),
        "logs_dir": str(logs_dir),
    }


def _property_xml(name: str, value: str) -> str:
    return f'        <property name="{escape(name, {chr(34): "&quot;"})}" value="{escape(value, {chr(34): "&quot;"})}"/>'


def _write_merged_junit_xml(path, evidence: dict) -> None:
    """Write a synthetic merged JUnit XML: 1 passed, 1 failed (full evidence), 1 errored (no evidence)."""
    failed_props = "\n".join(
        [
            _property_xml("Terminate", "no"),
            _property_xml("Feature File", "/runs/login.feature"),
            _property_xml("Output File", "/runs/login_output.txt"),
            _property_xml("Proofs Video", evidence["video"]),
            _property_xml(PROOFS_PROPERTY_NAME, evidence["proofs_dir"]),
            _property_xml("Proofs Screenshot", os.path.join(evidence["proofs_dir"], "screenshots")),
            _property_xml("Network Logs", evidence["network"]),
            _property_xml("Agents Internal Logs", evidence["logs_dir"]),
            _property_xml("Planner Thoughts", evidence["planner"]),
            _property_xml("Long Detail", LONG_PROPERTY_VALUE),
        ]
    )
    missing = str(path.parent / "does_not_exist")
    errored_props = "\n".join(
        [
            _property_xml("Terminate", "no"),
            _property_xml("Proofs Video", ""),
            _property_xml(PROOFS_PROPERTY_NAME, os.path.join(missing, "proofs")),
            _property_xml("Proofs Screenshot", os.path.join(missing, "screenshots")),
            _property_xml("Network Logs", os.path.join(missing, "network_logs.json")),
            _property_xml("Agents Internal Logs", os.path.join(missing, "logs")),
            _property_xml("Planner Thoughts", os.path.join(missing, "planner_thoughts.json")),
        ]
    )
    xml = f"""<testsuites>
  <testsuite name="Feature: Filter Bikes" tests="3" failures="1" errors="1" skipped="0" time="47.0">
    <testcase name="Scenario: User filters bikes with max price" classname="Feature: Filter Bikes" time="1.5">
      <properties>
{_property_xml("Terminate", "yes")}
      </properties>
      <system-out>Final Response: ok</system-out>
    </testcase>
    <testcase name="Scenario: Search fails: bike count wrong" classname="Feature: Filter Bikes" time="42.5">
      <properties>
{failed_props}
      </properties>
      <failure message="Assertion failed: expected 3 bikes, got 2" type="AssertionError"></failure>
      <system-out>Final Response: got 2 bikes</system-out>
      <system-out>usage.total_cost: 0.12</system-out>
    </testcase>
    <testcase name="Scenario: Agent timeout" classname="Feature: Filter Bikes" time="3.0">
      <properties>
{errored_props}
      </properties>
      <error type="TimeoutError">TimeoutError: navigation exceeded 30000ms</error>
    </testcase>
  </testsuite>
</testsuites>
"""
    path.write_text(xml, encoding="utf-8")


def test_sanitize_name_matches_stake_id_rules() -> None:
    assert sanitize_name("Scenario: User buys / item\\2 v1.0") == "Scenario__User_buys___item_2_v1_0"


def test_negative_and_nan_junit_times_are_reported_as_zero(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    xml_path.write_text(
        """<testsuites>
  <testsuite name="Feature: bad times" tests="3" failures="3" errors="0" time="-1.5">
    <testcase name="Scenario: negative time" classname="Feature: bad times" time="-5">
      <failure type="AssertionError">boom</failure>
    </testcase>
    <testcase name="Scenario: nan time" classname="Feature: bad times" time="NaN">
      <failure type="AssertionError">boom</failure>
    </testcase>
    <testcase name="Scenario: negative fractional time" classname="Feature: bad times" time="-1.5">
      <failure type="AssertionError">boom</failure>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    cases = parse_junit_testcases(str(xml_path))

    assert [case["time"] for case in cases] == [0.0, 0.0, 0.0]
    bundles = asyncio.run(create_failure_bundles(str(xml_path)))
    for bundle in bundles:
        assert "**Duration:** 0.00s" in (Path(bundle) / "bundle.md").read_text(encoding="utf-8")


def test_resolve_bundle_dir_names_is_collision_free_and_stable() -> None:
    names = ["Signup page", "s0", "a/b", "a_b"]
    mapping = resolve_bundle_dir_names(names)

    # Already-unique inputs keep their legacy sanitized names.
    assert mapping["Signup page"] == "Signup_page"
    assert mapping["s0"] == "s0"
    # The colliding pair maps to two DISTINCT dir names, injectively.
    assert mapping["a/b"] != mapping["a_b"]
    assert len(set(mapping.values())) == len(names)


def test_resolve_bundle_dir_names_folds_case_and_normalization() -> None:
    """macOS/Windows filesystems match paths case- and normalization-insensitively.

    Two titles differing only by case or NFC/NFD spelling would get distinct dir
    names here yet silently overwrite each other on disk, so the claim key is
    folded (NFC + lower) and the second name gets a hash suffix. The returned
    dir names keep their original sanitized form.
    """
    nfd_cafe = unicodedata.normalize("NFD", "café test")
    assert nfd_cafe != "café test"  # NFD spelling really differs from NFC
    names = ["Café Test", "café test", nfd_cafe, "Totally Different"]
    mapping = resolve_bundle_dir_names(names)

    # Plain distinct names are unchanged.
    assert mapping["Totally Different"] == "Totally_Different"
    # The first claim keeps its legacy sanitized dir name (directories are not renamed).
    assert mapping["Café Test"] == "Café_Test"
    # The case-/normalization-equivalent siblings get distinct hash-suffixed dirs.
    for sibling in ("café test", nfd_cafe):
        assert mapping[sibling] != mapping["Café Test"]
        # Compare on the folded key: returned names keep the input's own
        # normalization form, so only the folded name is directly comparable.
        folded = unicodedata.normalize("NFC", mapping[sibling]).lower()
        assert folded.startswith("café_test_")
    # Folded the way macOS/Windows compare paths, all returned names stay distinct.
    folded = [unicodedata.normalize("NFC", name).lower() for name in mapping.values()]
    assert len(set(folded)) == len(folded)


def test_colliding_testcase_names_get_distinct_bundles(tmp_path) -> None:
    """'a/b' and 'a_b' sanitize to the same dir name; neither may overwrite the other."""
    xml_path = tmp_path / "final_result.xml"
    xml_path.write_text(
        f"""<testsuites>
  <testsuite name="Feature: collisions" tests="4" failures="4" errors="0" time="4.0">
    <testcase name="Signup page" classname="Feature: collisions" time="1.0">
      <failure type="AssertionError">signup failed</failure>
    </testcase>
    <testcase name="s0" classname="Feature: collisions" time="1.0">
      <failure type="AssertionError">s0 failed</failure>
    </testcase>
    <testcase name="a/b" classname="Feature: collisions" time="1.0">
      <failure type="AssertionError">slash failed</failure>
    </testcase>
    <testcase name="a_b" classname="Feature: collisions" time="1.0">
      <failure type="AssertionError">underscore failed</failure>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    bundles = asyncio.run(create_failure_bundles(str(xml_path)))

    # One bundle dir per testcase, all distinct (no duplicate entries).
    assert len(bundles) == 4
    assert len(set(bundles)) == 4
    names = {os.path.basename(bundle) for bundle in bundles}
    assert {"Signup_page", "s0"} <= names

    # Each colliding testcase kept its own failure message in its own bundle.
    md_by_message = {(Path(bundle) / "bundle.md").read_text(encoding="utf-8"): bundle for bundle in bundles}
    slash_bundle = md_by_message[next(md for md in md_by_message if "slash failed" in md)]
    underscore_bundle = md_by_message[next(md for md in md_by_message if "underscore failed" in md)]
    assert slash_bundle != underscore_bundle
    assert os.path.basename(slash_bundle) != os.path.basename(underscore_bundle)
    # Both colliding dirs derive from the shared sanitized name "a_b".
    assert {os.path.basename(slash_bundle), os.path.basename(underscore_bundle)} <= {name for name in names if name.startswith("a_b")}


def test_duplicate_failing_testcase_name_yields_one_bundle(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    duplicate_case = """    <testcase name="Scenario: twice" classname="Feature: dup" time="1.0">
      <failure type="AssertionError">failed</failure>
    </testcase>
"""
    xml_path.write_text(
        f"""<testsuites>
  <testsuite name="Feature: dup" tests="2" failures="2" errors="0" time="2.0">
{duplicate_case}{duplicate_case}  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    bundles = asyncio.run(create_failure_bundles(str(xml_path)))

    assert bundles == [os.path.join(str(tmp_path / "bundles"), "Scenario__twice")]
    assert len(bundles) == len(set(bundles))


def test_symlinked_evidence_never_reaches_bundle(tmp_path) -> None:
    evidence = _make_evidence_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_secret = outside / "secret.png"
    outside_secret.write_bytes(PNG_HEADER + b"TOP-SECRET")
    outside_console = outside / "outside_console.json"
    outside_console.write_bytes(b'{"evil": true}')

    proofs_dir = Path(evidence["proofs_dir"])
    # File symlink inside the screenshots dir pointing outside the tree
    # (its name mentions "fail", so it would land in the mandatory keep-list too).
    os.symlink(outside_secret, proofs_dir / "screenshots" / "fail_leak.png")
    # console_logs.json replaced by a symlink to an outside file.
    console = proofs_dir / "console_logs.json"
    console.unlink()
    os.symlink(outside_console, console)

    xml_path = tmp_path / "final_result.xml"
    _write_merged_junit_xml(xml_path, evidence)

    bundles = asyncio.run(create_failure_bundles(str(xml_path)))
    failed_bundle = next(bundle for bundle in bundles if "bike_count_wrong" in bundle)
    bundle_dir = tmp_path / failed_bundle

    # Regular files are still copied.
    assert (bundle_dir / "video.webm").read_bytes() == VIDEO_BYTES
    assert (bundle_dir / "network.json").read_bytes() == NETWORK_BYTES
    assert (bundle_dir / "screenshots" / "fail_first.png").read_bytes() == FAIL_FIRST_BYTES

    # Nothing from outside the tree leaked in, by name or by content.
    bundle_contents: list[bytes] = []
    for root, _dirs, files in os.walk(bundle_dir):
        for file_name in files:
            bundle_contents.append((Path(root) / file_name).read_bytes())
    assert not any(b"TOP-SECRET" in content for content in bundle_contents)
    assert not any(b'{"evil": true}' in content for content in bundle_contents)
    assert not (bundle_dir / "console.json").exists()
    assert not (bundle_dir / "screenshots" / "fail_leak.png").exists()


def test_parse_junit_testcases_statuses_and_fields(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    _write_merged_junit_xml(xml_path, _make_evidence_tree(tmp_path))

    cases = parse_junit_testcases(str(xml_path))

    assert [case["status"] for case in cases] == ["passed", "failed", "error"]
    assert [case["name"] for case in cases] == [
        "Scenario: User filters bikes with max price",
        "Scenario: Search fails: bike count wrong",
        "Scenario: Agent timeout",
    ]

    passed, failed, errored = cases
    assert passed["failure_message"] is None
    assert passed["properties"]["Terminate"] == "yes"
    assert passed["time"] == 1.5

    assert failed["failure_message"] == "Assertion failed: expected 3 bikes, got 2"
    assert failed["classname"] == "Feature: Filter Bikes"
    assert failed["properties"]["Terminate"] == "no"
    assert failed["properties"]["Network Logs"].endswith("network_logs.json")
    assert failed["time"] == 42.5
    assert "Final Response: got 2 bikes" in failed["system_out"]
    assert "usage.total_cost: 0.12" in failed["system_out"]

    assert errored["failure_message"] == "TimeoutError: navigation exceeded 30000ms"
    assert errored["system_out"] == ""


def test_create_failure_bundles_only_for_failed_testcases(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    _write_merged_junit_xml(xml_path, _make_evidence_tree(tmp_path))

    bundles = asyncio.run(create_failure_bundles(str(xml_path)))

    assert len(bundles) == 2
    bundle_names = {os.path.basename(bundle) for bundle in bundles}
    assert bundle_names == {"Scenario__Search_fails__bike_count_wrong", "Scenario__Agent_timeout"}
    # Default location is <junit xml dir>/bundles/<sanitized testcase name>.
    assert all(bundle.startswith(str(tmp_path / "bundles")) for bundle in bundles)
    assert all(os.path.isdir(bundle) for bundle in bundles)


def test_bundle_contains_expected_artifacts(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    evidence = _make_evidence_tree(tmp_path)
    _write_merged_junit_xml(xml_path, evidence)

    bundles = asyncio.run(create_failure_bundles(str(xml_path), max_screenshots=2))
    failed_bundle = next(bundle for bundle in bundles if "bike_count_wrong" in bundle)

    # Video copied and renamed.
    assert (tmp_path / failed_bundle / "video.webm").read_bytes() == VIDEO_BYTES
    # JSON logs copied with expected names.
    assert (tmp_path / failed_bundle / "network.json").read_bytes() == NETWORK_BYTES
    assert (tmp_path / failed_bundle / "planner_thoughts.json").read_bytes() == PLANNER_BYTES
    assert (tmp_path / failed_bundle / "console.json").read_bytes() == CONSOLE_BYTES
    assert (tmp_path / failed_bundle / "agent_inner_thoughts.json").read_bytes() == INNER_THOUGHTS_BYTES

    # Screenshots: two most recent renamed step_<nn>.png oldest-first, fail-named always kept.
    screenshots_dir = tmp_path / failed_bundle / "screenshots"
    assert set(os.listdir(screenshots_dir)) == {"step_01.png", "step_02.png", "fail_first.png"}
    assert (screenshots_dir / "step_01.png").read_bytes() == SHOT_C_BYTES
    assert (screenshots_dir / "step_02.png").read_bytes() == FINAL_STATE_BYTES
    assert (screenshots_dir / "fail_first.png").read_bytes() == FAIL_FIRST_BYTES

    # Trace copied, DOM entries extracted.
    assert (tmp_path / failed_bundle / "trace.zip").exists()
    dom_dir = tmp_path / failed_bundle / "dom"
    assert set(os.listdir(dom_dir)) == {"index.html", "final.html"}


def test_bundle_md_contains_failure_details_and_artifact_links(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    _write_merged_junit_xml(xml_path, _make_evidence_tree(tmp_path))

    bundles = asyncio.run(create_failure_bundles(str(xml_path), max_screenshots=2))
    failed_bundle = next(bundle for bundle in bundles if "bike_count_wrong" in bundle)
    md = (tmp_path / failed_bundle / "bundle.md").read_text(encoding="utf-8")

    assert "# Failure bundle: Scenario: Search fails: bike count wrong" in md
    assert "**Status:** failed" in md
    assert "Assertion failed: expected 3 bikes, got 2" in md
    assert "Unclassified (auto-classification pending)" in md
    assert "## Artifacts" in md
    assert "](video.webm)" in md
    assert "](network.json)" in md
    assert "](planner_thoughts.json)" in md
    assert "](screenshots/step_01.png)" in md
    assert "](trace.zip)" in md
    assert "| Terminate | no |" in md
    # Long property values are truncated.
    assert LONG_PROPERTY_VALUE not in md
    assert ("x" * 200 + "...") in md
    assert "## Suggested next steps" in md


def test_bundle_with_missing_evidence_does_not_crash(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    _write_merged_junit_xml(xml_path, _make_evidence_tree(tmp_path))

    bundles = asyncio.run(create_failure_bundles(str(xml_path)))
    errored_bundle = next(bundle for bundle in bundles if "Agent_timeout" in bundle)

    # No evidence files could be collected, but the bundle still exists.
    assert (tmp_path / errored_bundle / "bundle.md").is_file()
    assert not (tmp_path / errored_bundle / "video.webm").exists()
    assert not (tmp_path / errored_bundle / "network.json").exists()
    assert not (tmp_path / errored_bundle / "screenshots").exists()

    md = (tmp_path / errored_bundle / "bundle.md").read_text(encoding="utf-8")
    assert "# Failure bundle: Scenario: Agent timeout" in md
    assert "**Status:** error" in md
    assert "TimeoutError: navigation exceeded 30000ms" in md
    assert "Unclassified (auto-classification pending)" in md
    assert "(no artifacts could be collected for this failure)" in md


def test_bundle_md_escapes_hostile_name_and_neutralizes_fences(tmp_path) -> None:
    """Hostile testcase content must not inject headings or break code fences."""
    evil_name = "Scenario: evil | name"
    evil_message = "boom ```\nrm -rf /\n``` done"
    evil_system_out = "danger ``` out"
    xml_path = tmp_path / "final_result.xml"
    xml_path.write_text(
        f"""<testsuites>
  <testsuite name="Feature: hostile" tests="1" failures="1" errors="0" time="1.0">
    <testcase name="{escape(evil_name)}" classname="Feature: | hostile |" time="1.0">
      <failure type="AssertionError">{escape(evil_message)}</failure>
      <system-out>{escape(evil_system_out)}</system-out>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    bundles = asyncio.run(create_failure_bundles(str(xml_path)))

    assert len(bundles) == 1
    md = (tmp_path / bundles[0] / "bundle.md").read_text(encoding="utf-8")

    # Heading and feature line are pipe-escaped, so no table/heading injection.
    assert f"# Failure bundle: Scenario: evil \\| name" in md
    assert "- **Feature:** Feature: \\| hostile \\|" in md

    # Both fenced blocks negotiate a fence longer than any embedded backtick run.
    lines = md.splitlines()
    fence_positions = [index for index, line in enumerate(lines) if line == "````"]
    assert len(fence_positions) == 4  # failure message + system output, open + close
    failure_block = "\n".join(lines[fence_positions[0] + 1 : fence_positions[1]])
    system_block = "\n".join(lines[fence_positions[2] + 1 : fence_positions[3]])
    assert evil_message in failure_block
    assert evil_system_out in system_block


def test_output_dir_override_and_max_screenshots(tmp_path) -> None:
    xml_path = tmp_path / "final_result.xml"
    _write_merged_junit_xml(xml_path, _make_evidence_tree(tmp_path))
    custom_dir = tmp_path / "custom_bundles"

    bundles = asyncio.run(create_failure_bundles(str(xml_path), output_dir=str(custom_dir), max_screenshots=1))

    assert len(bundles) == 2
    assert all(bundle.startswith(str(custom_dir)) for bundle in bundles)
    failed_bundle = next(bundle for bundle in bundles if "bike_count_wrong" in bundle)
    screenshots_dir = tmp_path / failed_bundle / "screenshots"
    # Only the single most recent screenshot plus the mandatory fail-named one.
    assert set(os.listdir(screenshots_dir)) == {"step_01.png", "fail_first.png"}
    assert (screenshots_dir / "step_01.png").read_bytes() == FINAL_STATE_BYTES


def test_create_run_bundles_uses_newest_run_dir(tmp_path) -> None:
    evidence = _make_evidence_tree(tmp_path)
    output_root = tmp_path / "optroot"
    older_dir = output_root / "output" / "run_0001"
    newer_dir = output_root / "output" / "run_0002"
    older_dir.mkdir(parents=True)
    newer_dir.mkdir(parents=True)
    _write_merged_junit_xml(older_dir / "final_result.xml", evidence)
    _write_merged_junit_xml(newer_dir / "final_result.xml", evidence)
    _touch(older_dir, 1_700_000_000.0)
    _touch(newer_dir, 1_700_000_100.0)

    bundles = asyncio.run(create_run_bundles(str(output_root)))

    assert len(bundles) == 2
    assert all(bundle.startswith(str(newer_dir / "bundles")) for bundle in bundles)
    assert all(os.path.isdir(bundle) for bundle in bundles)


def test_create_run_bundles_without_output_returns_empty(tmp_path) -> None:
    assert asyncio.run(create_run_bundles(str(tmp_path / "nowhere"))) == []


def test_create_run_bundles_breaks_mtime_ties_by_path(tmp_path) -> None:
    """Identical mtimes must resolve deterministically, newest run name first."""
    evidence = _make_evidence_tree(tmp_path)
    output_root = tmp_path / "optroot"
    older_named_dir = output_root / "output" / "run_20260909_100000"
    newer_named_dir = output_root / "output" / "run_20260909_100001"
    older_named_dir.mkdir(parents=True)
    newer_named_dir.mkdir(parents=True)
    _write_merged_junit_xml(older_named_dir / "final_result.xml", evidence)
    _write_merged_junit_xml(newer_named_dir / "final_result.xml", evidence)
    identical = 1_700_000_500.0
    _touch(older_named_dir, identical)
    _touch(newer_named_dir, identical)

    bundles = asyncio.run(create_run_bundles(str(output_root)))

    assert bundles
    assert all(bundle.startswith(str(newer_named_dir / "bundles")) for bundle in bundles)


# ── hardening: lone CR escaping, infinite durations, malformed newest junit ──


def test_escape_inline_strips_lone_carriage_return() -> None:
    """A lone CR is a line break in CommonMark and would silently split markdown lines."""
    assert _escape_inline("a\rb") == "a b"
    assert "\r" not in _escape_inline("a\r\nb\nc\rd")


def test_infinite_junit_time_reported_as_zero(tmp_path) -> None:
    """Positive-infinity junit times must clamp to 0 like NaN, not print "Duration: infs"."""
    xml_path = tmp_path / "final_result.xml"
    xml_path.write_text(
        """<testsuites>
  <testsuite name="Feature: bad times" tests="1" failures="1" errors="0" time="inf">
    <testcase name="Scenario: infinite time" classname="Feature: bad times" time="inf">
      <failure type="AssertionError">boom</failure>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    cases = parse_junit_testcases(str(xml_path))

    assert cases[0]["time"] == 0.0
    bundles = asyncio.run(create_failure_bundles(str(xml_path)))
    md = (Path(bundles[0]) / "bundle.md").read_text(encoding="utf-8")
    assert "**Duration:** 0.00s" in md
    assert "infs" not in md


def test_create_run_bundles_skips_malformed_junit_and_uses_newest_readable(tmp_path) -> None:
    """A malformed *_result.xml must not abort bundling while readable candidates exist."""
    evidence = _make_evidence_tree(tmp_path)
    output_root = tmp_path / "optroot"
    run_dir = output_root / "output" / "run_0001"
    run_dir.mkdir(parents=True)
    older_valid = run_dir / "a_result.xml"
    newest_malformed = run_dir / "z_result.xml"
    newest_valid = run_dir / "m_result.xml"
    _write_merged_junit_xml(older_valid, evidence)
    _write_merged_junit_xml(newest_valid, evidence)
    newest_malformed.write_text("<testsuites><testsuite><testcase broken", encoding="utf-8")
    _touch(older_valid, 1_700_000_000.0)
    _touch(newest_valid, 1_700_000_100.0)
    _touch(newest_malformed, 1_700_000_200.0)

    bundles = asyncio.run(create_run_bundles(str(output_root)))

    # Bundles were still produced from the newest READABLE candidate, no raise.
    assert len(bundles) == 2
    assert all(bundle.startswith(str(run_dir / "bundles")) for bundle in bundles)
    assert all(os.path.isdir(bundle) for bundle in bundles)
