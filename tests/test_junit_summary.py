from tests.test_base import compare_results
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator


def test_compare_results_parses_junit_summary_counts(tmp_path) -> None:
    expected_file = tmp_path / "expected_results.txt"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    actual_file = output_dir / "test.feature_result.xml"

    expected_file.write_text(
        'tests="2" errors="0" failures="1" skipped="0"\n',
        encoding="utf-8",
    )
    actual_file.write_text(
        '<testsuites><testsuite tests="2" errors="0" failures="1" skipped="0" /></testsuites>',
        encoding="utf-8",
    )

    assert compare_results(str(expected_file), str(output_dir)) is True


def test_junit_cost_metrics_accept_scalar_totals(tmp_path) -> None:
    generator = JUnitXMLGenerator(
        suite_name="Feature",
        feature_file_path=str(tmp_path / "test.feature"),
        output_file_path=str(tmp_path / "result.xml"),
        proofs_path=str(tmp_path),
        proofs_video_path="",
        proofs_screenshot_path="",
        network_logs_path="",
        logs_path="",
        planner_thoughts_path="",
    )

    generator.add_test_case(
        scenario="Scenario",
        feature="Feature",
        json_data={
            "terminate": "yes",
            "final_response": "ok",
            "is_assert": True,
            "is_passed": True,
        },
        execution_time=1.0,
        cost_metric={
            "usage_including_cached_inference": {
                "total_cost": 0.12,
                "langgraph": {
                    "cost": 0.12,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        },
    )

    assert generator.total_execution_cost == 0.12
    assert generator.total_token_used == 15
