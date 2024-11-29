import asyncio
import os
import platform
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Annotated, Any, List, Optional, Tuple

import aiohttp
from inflection import parameterize
from testzeus_hercules.config import get_proof_path
from testzeus_hercules.core.tools.tool_registry import sec_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

# Define cache and binary paths
CACHE_DIR = Path(os.environ["HF_HOME"]) / "nuclei_tool"
NUCLEI_BINARY = CACHE_DIR / "nuclei"


NUCLEI_RELEASE_API_URL = "https://api.github.com/repos/projectdiscovery/nuclei/releases/latest"
NUCLEI_DOWNLOAD_URL_TEMPLATE = "https://github.com/projectdiscovery/nuclei/releases/download/{version}/{filename}"

security_terms_explanation = {
    "cve": "Common Vulnerabilities and Exposures, a standardized list of publicly disclosed security vulnerabilities.",
    "panel": "Admin panels or dashboards, often targeted in penetration testing to exploit misconfigurations or weak authentication.",
    "wordpress": "A popular CMS frequently targeted due to its widespread use and vulnerable plugins/themes.",
    "exposure": "The accidental or intentional disclosure of sensitive information, key in security assessments.",
    "xss": "Cross-Site Scripting, a vulnerability that allows attackers to inject malicious scripts into web pages.",
    "osint": "Open Source Intelligence, gathering information from public sources for security analysis.",
    "tech": "Technology stacks or implementations, often assessed for vulnerabilities.",
    "misconfig": "Misconfigurations caused by improper setup of systems or applications, a common vulnerability.",
    "lfi": "Local File Inclusion, a vulnerability allowing attackers to include and execute server files.",
    "rce": "Remote Code Execution, enabling attackers to execute arbitrary code on a target system.",
    "edb": "Exploit-DB, a repository of publicly available exploits used by security researchers.",
    "packetstorm": "Packet Storm Security, offering tools, advisories, and exploits for security research.",
    "devops": "Development and operations pipelines, often assessed for vulnerabilities in tools and workflows.",
    "sqli": "SQL Injection, allowing attackers to manipulate SQL queries and access sensitive data.",
    "cloud": "Cloud infrastructure requiring measures to protect data from breaches and attacks.",
    "unauth": "Unauthorized access, where attackers gain system access without proper credentials.",
    "authenticated": "Authenticated testing, performed with valid credentials to assess internal security.",
    "intrusive": "Intrusive methods actively exploiting vulnerabilities to determine risk extent.",
}


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


async def get_latest_nuclei_version(
    base_url: str = NUCLEI_RELEASE_API_URL,
) -> str:
    """Get the latest Nuclei release version from GitHub API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(base_url) as resp:
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

    version = await get_latest_nuclei_version(base_url=NUCLEI_RELEASE_API_URL)
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

    nuclei_url = NUCLEI_DOWNLOAD_URL_TEMPLATE.format(version=version, filename=nuclei_filename)

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
    logger.info("Nuclei binary downloaded and installed.")
    file_logger("Nuclei binary downloaded and installed.")


async def run_nuclei_command(
    is_open_api_spec: bool,
    open_api_spec_path: Optional[str],
    target_url: Optional[str],
    tag: str,
    output_file: Path,
    headers: Optional[List[Tuple[str, str]]] = None,
    extra_args: Optional[list] = None,
) -> Tuple[str, str, int]:
    """Run a Nuclei command and return stdout, stderr, and return code."""
    await ensure_nuclei_installed()

    command = [
        str(NUCLEI_BINARY),
        "-skip-format-validation",
        "-v",
        "-tags",
        tag,
        "-o",
        str(output_file),
    ]

    if is_open_api_spec:
        command.extend(["-im", "openapi"])
        if open_api_spec_path:
            command.extend(["-l", open_api_spec_path])
        else:
            error_message = "open_api_spec_path is required when is_open_api_spec is True"
            logger.error(error_message)
            file_logger(error_message)
            raise Exception(error_message)
    else:
        if target_url:
            command.extend(["-u", target_url])
        else:
            error_message = "target_url is required when is_open_api_spec is False"
            logger.error(error_message)
            file_logger(error_message)
            raise Exception(error_message)

    if headers:
        for key, value in headers:
            header_string = f"{key}: {value}"
            command.extend(["-H", header_string])
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


def create_headers(
    bearer_token: Optional[str],
    header_tokens: Optional[List[str]],
    jwt_token: Optional[str],
    start_time: float,
) -> Tuple[Optional[List[Tuple[str, str]]], Optional[Tuple[Any, float]]]:
    headers_list = []
    if bearer_token:
        headers_list.append(("Authorization", f"Bearer {bearer_token}"))
    if header_tokens:
        for header in header_tokens:
            if "=" in header:
                key, value = header.split("=", 1)
                headers_list.append((key.strip(), value.strip()))
            else:
                error_message = f"Invalid header_token format: {header}. Expected 'Key=Value'."
                logger.error(error_message)
                file_logger(error_message)
                end_time = time.perf_counter()
                duration = end_time - start_time
                return None, ({"error": error_message}, duration)
    if jwt_token:
        headers_list.append(("Authorization", f"JWT {jwt_token}"))
    headers = headers_list if headers_list else None
    return headers, None


# Define distinct tool functions for each tag
for tag, explanation in security_terms_explanation.items():
    tool_name = parameterize(explanation.split(",")[0].lower()).replace("-", "_")[:32]

    # Define a unique function for each tag
    def create_tool_function(
        tag: str,
        explanation: str,
    ) -> Any:
        async def tool_function(
            is_open_api_spec: Annotated[
                bool,
                "Is the input an OpenAPI spec (True) or a target URL (False)? If true, open_api_spec_path is required with the path of the file.",
            ],
            target_url: Annotated[
                Optional[str],
                "Target URL to test (required if is_open_api_spec is False).",
            ] = None,
            open_api_spec_path: Annotated[
                Optional[str],
                "Path to the OpenAPI spec file (required if is_open_api_spec is True).",
            ] = None,
            bearer_token: Annotated[Optional[str], "Optional Bearer token for authentication."] = None,
            header_tokens: Annotated[
                Optional[List[str]],
                "Optional list of header tokens in 'Key=Value' format.",
            ] = None,
            jwt_token: Annotated[Optional[str], "Optional JWT token for authentication."] = None,
            # output_dir: Annotated[
            #     Optional[str], "Optional output directory for results."
            # ] = "nuclei_results",
        ) -> Annotated[
            Tuple[dict, float],
            "Result dictionary and time taken for the test in seconds.",
        ]:
            """
            Specific test tool function.
            """
            start_time = time.perf_counter()
            try:
                OUTPUT_PATH = get_proof_path() + "/nuclei_results"
                output_path = Path(OUTPUT_PATH)
                output_path.mkdir(parents=True, exist_ok=True)

                headers, error = create_headers(bearer_token, header_tokens, jwt_token, start_time)
                if error:
                    return {"error": error[0]}, error[1]

                output_file = output_path / f"{tag}.txt"

                stdout, stderr, returncode = await run_nuclei_command(
                    is_open_api_spec,
                    open_api_spec_path,
                    target_url,
                    tag,
                    output_file,
                    headers,
                )

                if returncode != 0:
                    error_message = stderr.strip()
                    logger.error(f"Nuclei command failed: {error_message}")
                    file_logger(f"Nuclei command failed: {error_message}")
                    end_time = time.perf_counter()
                    duration = end_time - start_time
                    return {"error": error_message}, duration

                with open(output_file, "r") as f:
                    content = f.read()

                logger.info("Nuclei command completed successfully.")
                file_logger("Nuclei command completed successfully.")

                end_time = time.perf_counter()
                duration = end_time - start_time
                if content:
                    return (
                        {
                            # "message": f"Test completed. output saved in {output_file}",
                            "test_result": content,
                        },
                        duration,
                    )
                else:
                    return (
                        {
                            "test_result": "No failures.",
                        },
                        duration,
                    )
            except Exception as e:
                logger.error(f"An unexpected error occurred: {e}")
                file_logger(f"An unexpected error occurred: {e}")
                end_time = time.perf_counter()
                duration = end_time - start_time
                return {"error": str(e)}, duration

        return tool_function

    # Dynamically create the function
    func = create_tool_function(tag, explanation)

    # Assign the dynamically created function a unique name in the global scope
    func.__name__ = tool_name
    globals()[tool_name] = tool(
        agent_names=["sec_nav_agent"],
        name=tool_name,
        description=f"Test for {explanation}",
    )(func)
