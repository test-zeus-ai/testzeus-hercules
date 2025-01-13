import json
import sys


def generate_features_report(virtuoso_data: dict) -> str:
    """
    Generate a human-readable test steps report where:
      - 'journeys' are called 'Features'
      - 'cases'   are called 'Scenarios'

    Args:
        virtuoso_data: Dictionary containing test data

    Returns:
        str: A single string that can be written to a .txt file or printed.
    """

    lines = []

    # Instead of journeys, call them 'features'
    features = virtuoso_data.get("journeys", [])
    for feature_idx, feature in enumerate(features, start=1):
        # Use the "title" as the feature name
        feature_title = feature.get("title", "Untitled Feature")

        lines.append(f"\n=== FEATURE #{feature_idx}: {feature_title} ===")

        # Inside each feature, we have "cases" -> call these Scenarios
        scenarios = feature.get("cases", [])
        for scenario_idx, scenario_obj in enumerate(scenarios, start=1):
            scenario_title = scenario_obj.get("title", "Untitled Scenario")
            lines.append(f"\n  -> Scenario #{scenario_idx}: {scenario_title}")

            # Steps in each scenario
            steps = scenario_obj.get("steps", [])
            for step_idx, step in enumerate(steps, start=1):
                action = step.get("action", "")
                value = step.get("value", "")
                expression = step.get("expression", "")
                optional_str = "(Optional)" if step.get("optional", False) else ""

                # The target text might be a user-visible label or content
                target = step.get("target", {})
                target_text = target.get("text", "")

                # Extract "GUESS" clues from the selectors (useful hints for manual testers)
                guess_clues = []
                selectors = target.get("selectors", [])
                for sel in selectors:
                    if sel.get("type") == "GUESS":
                            try:
                                clue_dict = json.loads(raw_val)
                                clue_text = clue_dict.get("clue", raw_val)
                            except json.JSONDecodeError:
                                pass
                                clue_dict = json.loads(raw_val)
                                clue_text = clue_dict.get("clue", raw_val)
                            except:
                                pass
                        guess_clues.append(clue_text)

                lines.append(f"\n    Step {step_idx} {optional_str}")
                lines.append(f"      Action     : {action}")

                if value:
                    lines.append(f"      Value      : {value}")
                if expression:
                    lines.append(f"      Expression : {expression}")
                if target_text:
                    lines.append(f"      TargetText : {target_text}")

                if guess_clues:
                    lines.append("      Guess Clues:")
                    for clue in guess_clues:
                        lines.append(f"         - {clue}")

    return "\n".join(lines)


if __name__ == "__main__":
    """
    Usage example:
    python script.py raw_jsons/test1.json output_report.txt
    """

    # Safely handle arguments or fallback to default file names
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        input_file = "raw_jsons/test2.json"  # or some default

    if len(sys.argv) > 2:
        output_file = sys.argv[2]
    else:
        output_file = "output_jsons/test_report2.txt"

    # Load the JSON
    with open(input_file, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # Generate text report
    report = generate_features_report(raw_data)

    # Write it to a text file
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report)

    # Optionally print to console
    print("Test steps report generated!\n")
    print(report)
