import json
import os
import traceback
from typing import Annotated, Callable, Union

import yaml
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import tool

# Refused when the platform supports it (POSIX): opening a path whose final
# component is a symlink raises ELOOP instead of following the link.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

_FILE_TOOLS_BOUNDARY_ERROR = "Access denied: '{path}' resolves to '{resolved}' which is outside the workspace root. " "Only paths inside '{root}' are allowed."


def _workspace_root() -> str:
    """Return the absolute workspace root that the findings file tools are confined to."""
    return os.path.realpath(get_global_conf().get_project_source_root())


def _resolve_in_workspace(file_path: str) -> tuple[str, str]:
    """Resolve ``file_path`` against the workspace root and enforce the boundary.

    Accepts absolute or relative paths; relative paths are treated as rooted at
    the workspace. Returns ``(candidate, resolved)``: ``candidate`` is the
    path to open as requested (absolute, still containing any final symlink
    component so O_NOFOLLOW can refuse it on write) and ``resolved`` is the
    fully symlink-resolved path used for the boundary check. Raises ValueError
    when the path escapes the workspace root (including ``..`` traversal and
    symlinks that point outside the workspace).
    """
    root = _workspace_root()
    candidate = file_path if os.path.isabs(file_path) else os.path.join(root, file_path)
    resolved = os.path.realpath(candidate)
    try:
        outside = os.path.commonpath([resolved, root]) != root
    except ValueError:  # e.g. paths on different drives on Windows
        outside = True
    if outside:
        raise ValueError(_FILE_TOOLS_BOUNDARY_ERROR.format(path=file_path, resolved=resolved, root=root))
    return candidate, resolved


def _safe_write(file_path: str, mode: str, writer: Callable) -> None:
    """Open ``file_path`` inside the workspace for writing and hand the file object to ``writer``.

    ``mode`` must be "w" (truncate) or "a" (append). On POSIX the open uses
    O_NOFOLLOW so a symlinked final path component is refused (ELOOP) instead
    of being followed.
    """
    flags = os.O_WRONLY | os.O_CREAT
    if mode == "a":
        flags |= os.O_APPEND
    else:
        flags |= os.O_TRUNC
    if _O_NOFOLLOW:
        flags |= _O_NOFOLLOW
    fd = os.open(file_path, flags, 0o644)
    with os.fdopen(fd, mode, encoding="utf-8") as f:
        writer(f)


def _ensure_parent_dir(file_path: str) -> None:
    """Create the parent directory of an in-workspace path if it is missing."""
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


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
        "For TXT and LOG files the string is written directly. Returns a success or error message. "
        "file_path must be inside the workspace root; absolute paths or traversals outside it are rejected."
    ),
)
def persist_findings(
    file_path: Annotated[str, "The path to the file where data should be written. Must be inside the workspace root."],
    data: Annotated[
        str,
        "Data to write as a string. For JSON/YAML, it must be a valid JSON/YAML representation of a dict or list; for TXT/LOG, any string.",
    ],
) -> Annotated[str, "A success message or an error message if the operation fails."]:
    """Write ``data`` to ``file_path`` inside the workspace root.

    The path is resolved against the workspace root (PROJECT_SOURCE_ROOT) and
    rejected if it escapes it. Symlinked write targets are refused on POSIX.
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        candidate, _resolved = _resolve_in_workspace(file_path)
        if ext == ".json":
            try:
                parsed_data = json.loads(data)
            except Exception as e:

                traceback.print_exc()
                raise ValueError("Provided data is not valid JSON: " + str(e))
            if not isinstance(parsed_data, (dict, list)):
                raise ValueError("For JSON files, data must represent a dictionary or a list.")
            _ensure_parent_dir(candidate)
            _safe_write(candidate, "w", lambda f: json.dump(parsed_data, f, indent=4))
        elif ext in [".yaml", ".yml"]:
            try:
                parsed_data = yaml.safe_load(data)
            except Exception as e:

                traceback.print_exc()
                raise ValueError("Provided data is not valid YAML: " + str(e))
            if not isinstance(parsed_data, (dict, list)):
                raise ValueError("For YAML files, data must represent a dictionary or a list.")
            _ensure_parent_dir(candidate)
            _safe_write(candidate, "w", lambda f: yaml.safe_dump(parsed_data, f))
        elif ext in [".txt", ".log"]:
            _ensure_parent_dir(candidate)
            _safe_write(candidate, "w", lambda f: f.write(data))
        else:
            raise ValueError(f"Unsupported file extension: {ext}")
        return f"Successfully wrote data to {file_path}"
    except Exception as e:

        traceback.print_exc()
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
        "operation fails. file_path must be inside the workspace root; absolute paths or traversals outside it are rejected."
    ),
)
def recall_findings(
    file_path: Annotated[str, "The path to the file from which data should be read. Must be inside the workspace root."],
) -> Annotated[
    Union[dict, list, str],
    "The file content (parsed object for JSON/YAML or string for TXT/LOG) or an error message.",
]:
    """Read ``file_path`` inside the workspace root.

    The path is resolved against the workspace root (PROJECT_SOURCE_ROOT) and
    rejected if it escapes it (including symlinks that point outside).
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        _candidate, target = _resolve_in_workspace(file_path)
        with open(target, "r", encoding="utf-8") as f:
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

        traceback.print_exc()
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
        "Returns a success message or an error message if the operation fails. "
        "file_path must be inside the workspace root; absolute paths or traversals outside it are rejected."
    ),
)
def augment_findings(
    file_path: Annotated[str, "The path to the file to which data should be appended. Must be inside the workspace root."],
    data: Annotated[
        str,
        "Data to append as a string. For JSON/YAML, it must be a valid representation of a dict or list; for TXT/LOG, any string.",
    ],
) -> Annotated[str, "A success message or an error message if the operation fails."]:
    """Append ``data`` to ``file_path`` inside the workspace root.

    The path is resolved against the workspace root (PROJECT_SOURCE_ROOT) and
    rejected if it escapes it. Symlinked write targets are refused on POSIX.
    """
    ext = os.path.splitext(file_path)[1].lower()
    try:
        candidate, target = _resolve_in_workspace(file_path)
        if not os.path.exists(target):
            # File does not exist; simply write the file.
            return persist_findings(file_path, data)

        if ext == ".json":
            with open(target, "r", encoding="utf-8") as f:
                try:
                    existing_data = json.load(f)
                except json.JSONDecodeError:
                    existing_data = None
            try:
                new_data_parsed = json.loads(data)
            except Exception as e:

                traceback.print_exc()
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
            _safe_write(candidate, "w", lambda f: json.dump(new_data, f, indent=4))

        elif ext in [".yaml", ".yml"]:
            with open(target, "r", encoding="utf-8") as f:
                try:
                    existing_data = yaml.safe_load(f)
                except yaml.YAMLError:
                    existing_data = None
            try:
                new_data_parsed = yaml.safe_load(data)
            except Exception as e:

                traceback.print_exc()
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
            _safe_write(candidate, "w", lambda f: yaml.safe_dump(new_data, f))

        elif ext in [".txt", ".log"]:
            _safe_write(candidate, "a", lambda f: f.write(data))
        else:
            raise ValueError(f"Unsupported file extension: {ext}")

        return f"Successfully appended data to {file_path}"
    except Exception as e:

        traceback.print_exc()
        return f"Error appending file: {e}"
