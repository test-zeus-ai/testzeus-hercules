# tool_registry.py
import csv
import hashlib
import os
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from testzeus_hercules.config import get_global_conf
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

    # Use CONF for path management
    proof_path = get_global_conf().get_proof_path()
    accessibility_logs_path = os.path.join(proof_path, "accessibility_logs.csv")
    with open(accessibility_logs_path, mode="w", newline="", encoding="utf-8") as csv_file:
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
    proof_path = get_global_conf().get_proof_path()
    json_accessibility_dom_path = os.path.join(proof_path, "json_accessibility_dom.json")
    with open(json_accessibility_dom_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def api_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = get_global_conf().get_proof_path()
    api_logs_path = os.path.join(proof_path, "api_logs.log")
    with open(api_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def sec_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = get_global_conf().get_proof_path()
    security_logs_path = os.path.join(proof_path, "security_logs.log")
    with open(security_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def assertion_logger(logging_string: str) -> None:
    """
    Append one line to the assertion audit ledger (proofs/assertion_logs.log).

    Every deterministic assertion verdict — PASSED included — is recorded here
    so reporting can distinguish "the agent verified the value" from "the
    agent narrated a pass". One JSON object per line.

    Parameters:
    - logging_string (str): The JSON line to append.
    """
    proof_path = get_global_conf().get_proof_path()
    assertion_logs_path = os.path.join(proof_path, "assertion_logs.log")
    with open(assertion_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def assertion_subject(subject: str) -> str:
    """
    Return a short fingerprint (sha1[:12]) of the examined payload.

    The fingerprint lets the report side deduplicate retries of the same
    assertion target without folding away cross-page/cross-response checks
    against different payloads.

    Parameters:
    - subject (str): The examined payload (usually the minified response body).
    """
    return hashlib.sha1(subject.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:12]


def tool(agent_names: list[str], description: str, name: str | None = None) -> Callable[[toolType], toolType]:
    """
    Decorator for registering private tools.

    Parameters:
    - description: A string describing the tool's function.
    - name: Optional name to register the tool with. If not provided, the function's name will be used.

    Re-registering an (agent, tool name) pair that already exists replaces
    the previous entry instead of appending a duplicate, so a module purge +
    re-import keeps the registry idempotent with the latest definition.

    Returns:
    - A decorator function that registers the tool in the global registry.
    """

    def decorator(func: toolType) -> toolType:
        tool_name = name if name else func.__name__  # Use provided name or fallback to function name
        entry = {
            "name": tool_name,
            "func": func,
            "description": description,
        }
        for agent_name in agent_names:
            agent_entries = tool_registry[agent_name]
            for index, existing in enumerate(agent_entries):
                if existing.get("name") == tool_name:
                    agent_entries[index] = entry
                    break
            else:
                agent_entries.append(entry)
        return func

    return decorator
