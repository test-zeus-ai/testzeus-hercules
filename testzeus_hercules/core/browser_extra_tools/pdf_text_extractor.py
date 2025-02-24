import os
from typing import Annotated, Dict, List, Optional
import time
import fitz
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.generic_tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger
import requests


@tool(
    agent_names=["pdf_nav_agent"],
    description="Extract text from a PDF file.",
    name="extract_text_from_pdf",
)
def extract_text_from_pdf(
    pdf_path: Annotated[str, "Path to the PDF file."],
    page_numbers: Annotated[
        List[int], "List of page numbers to extract text from."
    ] = None,
) -> Annotated[Dict[str, str], "Result of the PDF text extraction."]:
    """
    Extract text from a PDF file.
    """
    try:
        # Open the PDF file
        pdf_document = fitz.open(pdf_path)

        # Get total number of pages
        total_pages = pdf_document.page_count

        # If no specific pages are requested, extract from all pages
        if not page_numbers:
            page_numbers = list(range(total_pages))

        # Validate page numbers
        valid_pages = [p for p in page_numbers if 0 <= p < total_pages]
        if not valid_pages:
            return {
                "error": f"No valid page numbers provided. PDF has {total_pages} pages."
            }

        # Extract text from specified pages
        extracted_text = {}
        for page_num in valid_pages:
            page = pdf_document[page_num]
            text = page.get_text()
            extracted_text[f"page_{page_num + 1}"] = text

        # Close the PDF
        pdf_document.close()

        return {
            "status": "success",
            "message": f"Successfully extracted text from {len(valid_pages)} pages",
            "text": extracted_text,
        }

    except Exception as e:
        logger.error(f"Error in extract_text_from_pdf: {str(e)}")
        return {"error": str(e)}


def download_pdf(pdf_url: str, file_path: str) -> str:
    """
    Download a PDF file from a URL.
    """
    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Download the PDF
        response = requests.get(pdf_url)
        response.raise_for_status()

        # Save the PDF
        with open(file_path, "wb") as f:
            f.write(response.content)

        return file_path

    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to download PDF from {pdf_url}: {str(e)}"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"Error downloading PDF: {str(e)}"
        logger.error(error_msg)
        return error_msg


def cleanup_temp_files(file_path: str) -> None:
    """
    Clean up temporary files.
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.debug(f"Successfully removed {file_path}")
        else:
            logger.debug(
                f"File not found. Unable to clean it from the filesystem: {file_path}"
            )
    except Exception as e:
        logger.error(f"Failed to remove {file_path}: {str(e)}")


@tool(
    agent_names=["pdf_nav_agent"],
    description="Search for text in a PDF file.",
    name="search_text_in_pdf",
)
def search_text_in_pdf(
    pdf_path: Annotated[str, "Path to the PDF file."],
    search_text: Annotated[str, "Text to search for in the PDF."],
    case_sensitive: Annotated[
        bool, "Whether the search should be case sensitive."
    ] = False,
) -> Annotated[Dict[str, str], "Result of the PDF text search."]:
    """
    Search for text in a PDF file.
    """
    try:
        # Open the PDF file
        pdf_document = fitz.open(pdf_path)

        # Get total number of pages
        total_pages = pdf_document.page_count

        # Search for text in all pages
        search_results = {}
        for page_num in range(total_pages):
            page = pdf_document[page_num]
            instances = page.search_for(
                search_text, flags=0 if case_sensitive else fitz.TEXT_ENCODING_LATIN
            )
            if instances:
                search_results[f"page_{page_num + 1}"] = len(instances)

        # Close the PDF
        pdf_document.close()

        if search_results:
            return {
                "status": "success",
                "message": f"Found text '{search_text}' in {len(search_results)} pages",
                "results": search_results,
            }
        else:
            return {
                "status": "warning",
                "message": f"Text '{search_text}' not found in the PDF",
            }

    except Exception as e:
        logger.error(f"Error in search_text_in_pdf: {str(e)}")
        return {"error": str(e)}
