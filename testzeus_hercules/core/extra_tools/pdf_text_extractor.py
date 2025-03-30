import os
import traceback
from typing import Annotated

import httpx
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger
from unstructured.partition.pdf import partition_pdf


@tool(
    agent_names=["browser_nav_agent"],
    description="""Extracts text from PDF at given URL.""",
    name="extract_text_from_pdf",
)
async def extract_text_from_pdf(
    pdf_url: Annotated[str, "URL of the PDF file to extract text from."],
) -> Annotated[str, "All the text found in the PDF file."]:
    """
    Extract text from a PDF file.
    pdf_url: str - The URL of the PDF file to extract text from.
    returns: str - All the text found in the PDF.
    """
    file_path = os.path.join(get_global_conf().get_project_temp_path(), "downloaded_file.pdf")  # fixed file path for downloading the PDF

    try:
        # Create and use the PlaywrightManager
        browser_manager = PlaywrightManager()

        # Download the PDF
        download_result = await download_pdf(pdf_url, file_path)
        if not os.path.exists(download_result):
            return download_result  # Return error message if download failed

        # Extract text using unstructured
        elements = partition_pdf(download_result)
        extracted_text = "\n".join([str(element) for element in elements])

        return "Text found in the PDF:\n" + extracted_text
    except httpx.HTTPStatusError as e:
        logger.error(f"An error occurred while downloading the PDF from {pdf_url}: {str(e)}")
        return f"An error occurred while downloading the PDF: {str(e)}"
    except Exception as e:

        traceback.print_exc()
        logger.error(f"An error occurred while extracting text from the PDF that was downloaded from {pdf_url}: {str(e)}")
        return f"An error occurred while extracting text: {str(e)}"
    finally:
        # Cleanup: Ensure the downloaded file is removed
        cleanup_temp_files(file_path)


def cleanup_temp_files(*file_paths: str) -> None:
    """
    Remove the specified temporary files.

    *file_paths: str - One or more file paths to be removed.
    """
    for file_path in file_paths:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.debug(f"Cleaned file from the filesystem: {file_path}")
            except Exception as e:

                traceback.print_exc()
                logger.error(f"Failed to remove {file_path}: {str(e)}")
        else:
            logger.debug(f"File not found. Unable to clean it from the filesystem: {file_path}")


async def download_pdf(pdf_url: str, file_path: str) -> str:
    """
    Download the PDF file from the given URL and save it to the specified path.

    pdf_url: str - The URL of the PDF file to download.
    file_path: str - The local path to save the downloaded PDF.

    returns: str - The file path of the downloaded PDF if successful, otherwise an error message.
    raises: Exception - If an error occurs during the download process.
    """
    try:
        logger.info(f"Downloading PDF from: {pdf_url} to: {file_path}")
        async with httpx.AsyncClient() as client:
            response = await client.get(pdf_url)
            response.raise_for_status()  # Ensure the request was successful
        with open(file_path, "wb") as pdf_file:
            pdf_file.write(response.content)
        return file_path
    # except httpx.HTTPStatusError as e:
    #     raise e
    except Exception as e:

        traceback.print_exc()
        raise e
