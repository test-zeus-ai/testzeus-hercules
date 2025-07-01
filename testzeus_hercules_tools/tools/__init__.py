"""
Tools module for testzeus_hercules_tools package.
Contains dual-mode browser interaction and specialized operation tools.
"""

from .click import click_element
from .input import enter_text
from .hover import hover_element
from .dropdown import select_dropdown
from .navigation import open_url
from .keyboard import press_key_combination
from .file_upload import upload_file
from .page_content import get_page_text, get_interactive_elements

try:
    from .sql_operations import execute_select_query
    _HAS_SQL = True
except ImportError:
    _HAS_SQL = False
    execute_select_query = None

try:
    from .api_operations import http_request
    _HAS_API = True
except ImportError:
    _HAS_API = False
    http_request = None

try:
    from .accessibility_operations import test_page_accessibility
    _HAS_ACCESSIBILITY = True
except ImportError:
    _HAS_ACCESSIBILITY = False
    test_page_accessibility = None

from .time_operations import wait_for_seconds, wait_until_condition

try:
    from .security_operations import (
        run_security_scan,
        scan_for_cve,
        scan_for_xss,
        scan_for_sqli,
        scan_for_rce,
        scan_for_lfi
    )
    _HAS_SECURITY = True
except ImportError:
    _HAS_SECURITY = False
    run_security_scan = None
    scan_for_cve = None
    scan_for_xss = None
    scan_for_sqli = None
    scan_for_rce = None
    scan_for_lfi = None

from .logger import InteractionLogger
from .code_generator import CodeGenerator

__all__ = [
    "click_element",
    "enter_text", 
    "hover_element",
    "select_dropdown",
    "open_url",
    "press_key_combination",
    "upload_file",
    "get_page_text",
    "get_interactive_elements",
    
    "wait_for_seconds",
    "wait_until_condition",
    
    "InteractionLogger",
    "CodeGenerator"
]

if _HAS_SQL and execute_select_query:
    __all__.append("execute_select_query")

if _HAS_API and http_request:
    __all__.append("http_request")

if _HAS_ACCESSIBILITY and test_page_accessibility:
    __all__.append("test_page_accessibility")

if _HAS_SECURITY:
    if run_security_scan:
        __all__.extend([
            "run_security_scan",
            "scan_for_cve",
            "scan_for_xss",
            "scan_for_sqli",
            "scan_for_rce",
            "scan_for_lfi"
        ])
