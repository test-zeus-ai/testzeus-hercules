import asyncio
import json
import os
import platform
import time
import tarfile
import zipfile
from pathlib import Path
from typing import Annotated, Any, Optional, Tuple

import aiohttp
from testzeus_hercules.core.tools.tool_registry import tool, sec_logger as file_logger
from testzeus_hercules.utils.logger import logger

CACHE_DIR = Path(os.environ["HF_HOME"]) / "nuclei_tool"
NUCLEI_BINARY = CACHE_DIR / "nuclei"
TEMPLATES_DIR = CACHE_DIR / "nuclei-templates"

NUCLEI_RELEASE_API_URL = (
    "https://api.github.com/repos/projectdiscovery/nuclei/releases/latest"
)
NUCLEI_TEMPLATES_URL = (
    "https://github.com/projectdiscovery/nuclei-templates/archive/refs/heads/master.zip"
)
NUCLEI_DOWNLOAD_URL_TEMPLATE = (
    "https://github.com/projectdiscovery/nuclei/releases/download/{version}/{filename}"
)


async def download_file(url: str, dest: Path) -> None:
    """Download a file from a URL to a destination path."""
    logger.info(f"Downloading {url} to {dest}")
    file_logger(f"Downloading {url} to {dest}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                while True:
                    chunk = await resp.content.read(1024)
                    if not chunk:
                        break
                    f.write(chunk)


async def get_latest_nuclei_version() -> str:
    """Get the latest Nuclei release version from GitHub API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(NUCLEI_RELEASE_API_URL) as resp:
            resp.raise_for_status()
            data = await resp.json()
            version = data["tag_name"]
            logger.info(f"Latest Nuclei version: {version}")
            file_logger(f"Latest Nuclei version: {version}")
            return version


async def ensure_nuclei_installed() -> None:
    """Ensure that Nuclei binary is installed in the cache directory."""
    if NUCLEI_BINARY.exists():
        logger.info("Nuclei binary already exists.")
        file_logger("Nuclei binary already exists.")
        return

    logger.info("Nuclei binary not found. Downloading...")
    file_logger("Nuclei binary not found. Downloading...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    system = platform.system()
    arch = platform.machine()

    version = await get_latest_nuclei_version()
    version_number = version.lstrip("v")  # Remove 'v' prefix if present

    if system == "Windows":
        nuclei_filename = f"nuclei_{version_number}_windows_amd64.zip"
        binary_name = "nuclei.exe"
    elif system == "Darwin":
        nuclei_filename = f"nuclei_{version_number}_macos_amd64.zip"
        binary_name = "nuclei"
    else:  # Assume Linux
        if "arm" in arch or "aarch64" in arch:
            nuclei_filename = f"nuclei_{version_number}_linux_arm64.zip"
        else:
            nuclei_filename = f"nuclei_{version_number}_linux_amd64.zip"
        binary_name = "nuclei"

    nuclei_url = NUCLEI_DOWNLOAD_URL_TEMPLATE.format(
        version=version, filename=nuclei_filename
    )

    archive_path = CACHE_DIR / nuclei_filename
    await download_file(nuclei_url, archive_path)

    # Extract the binary
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zip_ref:
            zip_ref.extract(binary_name, CACHE_DIR)
    else:
        with tarfile.open(archive_path, "r:gz") as tar_ref:
            tar_ref.extract(binary_name, path=CACHE_DIR)

    # Make the binary executable
    nuclei_binary_path = CACHE_DIR / binary_name
    nuclei_binary_path.chmod(0o755)
    if NUCLEI_BINARY.exists():
        NUCLEI_BINARY.unlink()
    NUCLEI_BINARY.symlink_to(nuclei_binary_path)
    archive_path.unlink()
    logger.info("Nuclei binary downloaded and installed.")
    file_logger("Nuclei binary downloaded and installed.")


async def ensure_templates_installed() -> None:
    """Ensure that Nuclei templates are installed in the cache directory."""
    if TEMPLATES_DIR.exists():
        logger.info("Nuclei templates already exist.")
        file_logger("Nuclei templates already exist.")
        return

    logger.info("Nuclei templates not found. Downloading...")
    file_logger("Nuclei templates not found. Downloading...")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    archive_path = CACHE_DIR / "nuclei-templates.zip"
    await download_file(NUCLEI_TEMPLATES_URL, archive_path)

    # Extract the templates
    with zipfile.ZipFile(archive_path, "r") as zip_ref:
        zip_ref.extractall(CACHE_DIR)

    # The extracted folder will be named 'nuclei-templates-master'
    extracted_folder = CACHE_DIR / "nuclei-templates-master"
    extracted_folder.rename(TEMPLATES_DIR)

    archive_path.unlink()
    logger.info("Nuclei templates downloaded and extracted.")
    file_logger("Nuclei templates downloaded and extracted.")


async def run_nuclei_command(
    base_url: str,
    template: str,
    output_file: Path,
    headers: Optional[str] = None,
    extra_args: Optional[list] = None,
) -> Tuple[str, str, int]:
    """Run a Nuclei command and return stdout, stderr, and return code."""
    await ensure_nuclei_installed()
    await ensure_templates_installed()

    template_path = TEMPLATES_DIR / template
    if not template_path.exists():
        error_message = f"Template {template} does not exist."
        logger.error(error_message)
        file_logger(error_message)
        raise Exception(error_message)

    command = [
        str(NUCLEI_BINARY),
        "-u",
        base_url,
        "-t",
        str(template_path),
        "-o",
        str(output_file),
    ]
    if headers:
        command.extend(["-H", headers])
    if extra_args:
        command.extend(extra_args)

    logger.info(f"Running Nuclei command: {' '.join(command)}")
    file_logger(f"Running Nuclei command: {' '.join(command)}")

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    stdout_decoded = stdout.decode()
    stderr_decoded = stderr.decode()

    file_logger(f"Nuclei command stdout: {stdout_decoded}")
    file_logger(f"Nuclei command stderr: {stderr_decoded}")

    return stdout_decoded, stderr_decoded, process.returncode


@tool(
    agent_names=["sec_nav_agent"],
    name="test_authentication_bypass",
    description="Test for authentication bypass vulnerabilities using Nuclei.",
)
async def test_authentication_bypass(
    base_url: Annotated[str, "The base URL to test."],
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    output_dir: Annotated[
        Optional[str], "Optional output directory to save results."
    ] = "nuclei_results",
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the result message or error and the time taken for the test in seconds.",
]:
    """Test for authentication bypass vulnerabilities using Nuclei."""
    start_time = time.perf_counter()
    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        headers = f"Authorization: Bearer {token}" if token else None

        template = "default-logins/auth-bypass.yaml"
        output_file = output_path / "auth_bypass.txt"

        stdout, stderr, returncode = await run_nuclei_command(
            base_url, template, output_file, headers
        )

        if returncode != 0:
            error_message = stderr.strip()
            logger.error(f"Nuclei command failed: {error_message}")
            file_logger(f"Nuclei command failed: {error_message}")
            end_time = time.perf_counter()
            duration = end_time - start_time
            return {"error": error_message}, duration

        logger.info("Nuclei command completed successfully.")
        file_logger("Nuclei command completed successfully.")

        end_time = time.perf_counter()
        duration = end_time - start_time
        return {"message": f"Test completed. Results saved in {output_file}"}, duration
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        file_logger(f"An unexpected error occurred: {e}")
        end_time = time.perf_counter()
        duration = end_time - start_time
        return {"error": str(e)}, duration


# Apply similar changes to the rest of the tool functions


@tool(
    agent_names=["sec_nav_agent"],
    name="test_sensitive_data_exposure",
    description="Test for sensitive data exposure using Nuclei.",
)
async def test_sensitive_data_exposure(
    base_url: Annotated[str, "The base URL to test."],
    token: Annotated[
        Optional[str],
        "Optional bearer or JWT token for authentication. Include the token string without the 'Bearer ' prefix.",
    ] = None,
    output_dir: Annotated[
        Optional[str], "Optional output directory to save results."
    ] = "nuclei_results",
) -> Annotated[
    Tuple[Any, float],
    "A tuple containing the result message or error and the time taken for the test in seconds.",
]:
    """Test for sensitive data exposure using Nuclei."""
    start_time = time.perf_counter()
    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        headers = f"Authorization: Bearer {token}" if token else None

        template = "exposures/configs/sensitive-data-exposure.yaml"
        output_file = output_path / "sensitive_data.txt"

        stdout, stderr, returncode = await run_nuclei_command(
            base_url, template, output_file, headers
        )

        if returncode != 0:
            error_message = stderr.strip()
            logger.error(f"Nuclei command failed: {error_message}")
            file_logger(f"Nuclei command failed: {error_message}")
            end_time = time.perf_counter()
            duration = end_time - start_time
            return {"error": error_message}, duration

        logger.info("Nuclei command completed successfully.")
        file_logger("Nuclei command completed successfully.")

        end_time = time.perf_counter()
        duration = end_time - start_time
        return {"message": f"Test completed. Results saved in {output_file}"}, duration
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        file_logger(f"An unexpected error occurred: {e}")
        end_time = time.perf_counter()
        duration = end_time - start_time
        return {"error": str(e)}, duration


# Repeat the addition of `file_logger` and use constants in the remaining tool functions...
