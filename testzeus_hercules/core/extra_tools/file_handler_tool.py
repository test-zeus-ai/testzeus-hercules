import json
import os
from typing import Annotated, Union

import yaml
from testzeus_hercules.core.tools.tool_registry import tool

# ------------------------------------------------------------------------------
# Persist Findings Tool (formerly write_file)
# ------------------------------------------------------------------------------


@tool(
    agent_names=["browser_nav_agent", "api_nav_agent"],
    name="persist_findings",
    description=(
        "Writes data to a file with the specified file_path. Supported file formats: "
        "JSON (.json), YAML (.yaml/.yml), TXT (.txt) and LOG (.log). The provided data must be a string. "
        "For JSON and YAML files the string should represent a dict or list (parsed via json.loads or yaml.safe_load). "
        "For TXT and LOG files the string is written directly. Returns a success or error message."
    ),
)
def persist_findings(
    file_path: Annotated[str, "The path to the file where data should be written."],
    data: Annotated[
        str,
        "Data to write as a string. For JSON/YAML, it must be a valid JSON/YAML representation of a dict or list; for TXT/LOG, any string.",
    ],
) -> Annotated[str, "A success message or an error message if the operation fails."]:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".json":
            try:
                parsed_data = json.loads(data)
            except Exception as e:
                raise ValueError("Provided data is not valid JSON: " + str(e))
            if not isinstance(parsed_data, (dict, list)):
                raise ValueError("For JSON files, data must represent a dictionary or a list.")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(parsed_data, f, indent=4)
        elif ext in [".yaml", ".yml"]:
            try:
                parsed_data = yaml.safe_load(data)
            except Exception as e:
                raise ValueError("Provided data is not valid YAML: " + str(e))
            if not isinstance(parsed_data, (dict, list)):
                raise ValueError("For YAML files, data must represent a dictionary or a list.")
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(parsed_data, f)
        elif ext in [".txt", ".log"]:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(data)
        else:
            raise ValueError(f"Unsupported file extension: {ext}")
        return f"Successfully wrote data to {file_path}"
    except Exception as e:
        return f"Error writing file: {e}"


# ------------------------------------------------------------------------------
# Recall Findings Tool (formerly read_file)
# ------------------------------------------------------------------------------


@tool(
    agent_names=["browser_nav_agent", "api_nav_agent"],
    name="recall_findings",
    description=(
        "Reads data from a file at the given file_path. For JSON and YAML files, returns the parsed "
        "Python object; for TXT and LOG files, returns the raw text content. Returns an error message if the "
        "operation fails."
    ),
)
def recall_findings(file_path: Annotated[str, "The path to the file from which data should be read."]) -> Annotated[
    Union[dict, list, str],
    "The file content (parsed object for JSON/YAML or string for TXT/LOG) or an error message.",
]:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if ext == ".json":
            return json.loads(content)
        elif ext in [".yaml", ".yml"]:
            return yaml.safe_load(content)
        elif ext in [".txt", ".log"]:
            return content
        else:
            raise ValueError(f"Unsupported file extension: {ext}")
    except Exception as e:
        return f"Error reading file: {e}"


# ------------------------------------------------------------------------------
# Augment Findings Tool (formerly append_file)
# ------------------------------------------------------------------------------


@tool(
    agent_names=["browser_nav_agent", "api_nav_agent"],
    name="augment_findings",
    description=(
        "Appends data to an existing file at the specified file_path. The provided data must be a string. "
        "For JSON and YAML files, the string must represent a dict or list. If the file contains a list, the "
        "new data is appended (or extended if the new data is a list); if the file contains a dict, the new data "
        "(must be a dict) is merged. For TXT and LOG files, the new data is appended directly. "
        "Returns a success message or an error message if the operation fails."
    ),
)
def augment_findings(
    file_path: Annotated[str, "The path to the file to which data should be appended."],
    data: Annotated[
        str,
        "Data to append as a string. For JSON/YAML, it must be a valid representation of a dict or list; for TXT/LOG, any string.",
    ],
) -> Annotated[str, "A success message or an error message if the operation fails."]:
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if not os.path.exists(file_path):
            # File does not exist; simply write the file.
            return persist_findings(file_path, data)

        if ext == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = None
            try:
                new_data_parsed = json.loads(data)
            except Exception as e:
                raise ValueError("Provided data is not valid JSON: " + str(e))
            if not isinstance(new_data_parsed, (dict, list)):
                raise ValueError("For JSON files, data must represent a dictionary or a list.")
            if existing_data is None:
                new_data = new_data_parsed
            elif isinstance(existing_data, list):
                if isinstance(new_data_parsed, list):
                    existing_data.extend(new_data_parsed)
                else:
                    existing_data.append(new_data_parsed)
                new_data = existing_data
            elif isinstance(existing_data, dict):
                if isinstance(new_data_parsed, dict):
                    existing_data.update(new_data_parsed)
                    new_data = existing_data
                else:
                    raise ValueError("Existing JSON file is a dict; new data must also be a dict to merge.")
            else:
                raise ValueError("Existing JSON file content must be a dictionary or a list to append.")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(new_data, f, indent=4)

        elif ext in [".yaml", ".yml"]:
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    existing_data = yaml.safe_load(f)
                except yaml.YAMLError:
                    existing_data = None
            try:
                new_data_parsed = yaml.safe_load(data)
            except Exception as e:
                raise ValueError("Provided data is not valid YAML: " + str(e))
            if not isinstance(new_data_parsed, (dict, list)):
                raise ValueError("For YAML files, data must represent a dictionary or a list.")
            if existing_data is None:
                new_data = new_data_parsed
            elif isinstance(existing_data, list):
                if isinstance(new_data_parsed, list):
                    existing_data.extend(new_data_parsed)
                else:
                    existing_data.append(new_data_parsed)
                new_data = existing_data
            elif isinstance(existing_data, dict):
                if isinstance(new_data_parsed, dict):
                    existing_data.update(new_data_parsed)
                    new_data = existing_data
                else:
                    raise ValueError("Existing YAML file is a dict; new data must also be a dict to merge.")
            else:
                raise ValueError("Existing YAML file content must be a dictionary or a list to append.")
            with open(file_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(new_data, f)

        elif ext in [".txt", ".log"]:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(data)
        else:
            raise ValueError(f"Unsupported file extension: {ext}")

        return f"Successfully appended data to {file_path}"
    except Exception as e:
        return f"Error appending file: {e}"
