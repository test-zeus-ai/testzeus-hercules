# tool_registry.py
import csv
import os
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from testzeus_hercules.config import get_proof_path
from testzeus_hercules.utils.logger import logger

# Define the type of the functions that will be registered as tools
toolType = Callable[..., Any]

# Global registry to store private tool functions and their metadata
#
tool_registry: dict[list[dict[str, Any]]] = defaultdict(list)


def accessibility_logger(identity: str, violations_json: dict) -> None:
    """
    Converts Axe-core accessibility violations JSON to CSV.

    Parameters:
    - violations_json (dict): The JSON object containing accessibility violations.

    Returns:
    None
    """
    og_identity = f"{identity}"

    # Extract violations from the JSON
    if isinstance(violations_json, list):
        violations = violations_json
    else:
        violations = violations_json.get("violations", [])

    if not violations:
        logger.info("No violations found in the provided JSON.")
        return

    # Define CSV header
    csv_header = [
        "Violation ID",
        "Impact",
        "Description",
        "Help URL",
        "Affected Element",
        "Fix Recommendation",
    ]

    # Open the CSV file for writing
    proof_path = os.path.join(get_proof_path(), "accessibility_logs.csv")
    with open(proof_path, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)

        # Write header row
        writer.writerow(csv_header)

        # Write each violation and affected nodes to the CSV
        for violation in violations:
            identity_page = og_identity
            violation_id = violation["id"]
            impact = violation.get("impact", "N/A")
            description = violation["description"]
            help_url = violation["helpUrl"]
            nodes = violation.get("nodes", [])

            for node in nodes:
                affected_element = node["html"]
                fix_recommendation = node["failureSummary"]

                writer.writerow(
                    [
                        identity_page,
                        violation_id,
                        impact,
                        description,
                        help_url,
                        affected_element,
                        fix_recommendation,
                    ]
                )

    logger.info(f"Accessibility violations successfully saved to {proof_path}.")


def accessibility_logger_json(identity: str, logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    # clean identity str
    identity = identity.replace("/", "").replace(":", "").lower().replace("#", "")
    proof_path = os.path.join(get_proof_path(), f"{identity}_accessibility_logs.json")
    with open(proof_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def api_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = os.path.join(get_proof_path(), "api_logs.log")
    with open(proof_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def sec_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = os.path.join(get_proof_path(), "sec_logs.log")
    with open(proof_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def tool(agent_names: list[str], description: str, name: str | None = None) -> Callable[[toolType], toolType]:
    """
    Decorator for registering private tools.

    Parameters:
    - description: A string describing the tool's function.
    - name: Optional name to register the tool with. If not provided, the function's name will be used.

    Returns:
    - A decorator function that registers the tool in the global registry.
    """

    def decorator(func: toolType) -> toolType:
        for agent_name in agent_names:
            tool_registry[agent_name].append(
                {
                    "name": (name if name else func.__name__),  # Use provided name or fallback to function name
                    "func": func,
                    "description": description,
                }
            )
        return func

    return decorator
