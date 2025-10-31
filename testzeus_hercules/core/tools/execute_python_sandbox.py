"""
Python Sandbox Execution Tool

Execute Python scripts in a controlled sandbox environment with automatic
Playwright browser automation and module injection support.
"""

import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Annotated, Dict

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger


class ExecutionTimeout(Exception):
    """Exception raised when execution times out."""
    pass


@tool(
    agent_names=["executor_nav_agent"],
    description="""Execute Python code from a file in a controlled sandbox environment with access to Playwright browser automation. 
    The sandbox automatically injects: playwright_manager, page, browser, context, asyncio, logger, and common Python packages.
    Perfect for running custom automation scripts, complex interactions, or multi-step workflows.
    The code file can contain both synchronous and asynchronous functions.
    Supports multi-tenant injections configured via environment variables.""",
    name="execute_python_sandbox",
)
async def execute_python_sandbox(
    file_path: Annotated[str, "Path to Python file to execute"],
    timeout_seconds: Annotated[float, "Maximum execution time in seconds"] = 300.0,
    function_name: Annotated[str, "Optional: Specific function to call (defaults to main or full script execution)"] = "",
    function_args: Annotated[str, "Optional: JSON string of arguments to pass to function (e.g., '{\"arg1\": \"value\", \"arg2\": 123}')"] = "{}",
) -> str:
    """
    Execute Python code from a file in a controlled sandbox environment.
    
    Args:
        file_path: Path to Python file (absolute or relative to project root)
        timeout_seconds: Maximum execution time (default 300s)
        function_name: Optional specific function to call
        function_args: Optional JSON string of function arguments
        
    Returns:
        JSON string with execution results
        
    Note:
        Tenant ID and custom injections are automatically determined from configuration:
        - SANDBOX_TENANT_ID: Determines tenant-specific module access
        - SANDBOX_CUSTOM_INJECTIONS: JSON string with custom modules/objects to inject
    """
    logger.info("Executing Python sandbox: file=%s, timeout=%ss", file_path, timeout_seconds)
    add_event(EventType.INTERACTION, EventData(detail="execute_python_sandbox"))
    
    # Get sandbox configuration
    config = get_global_conf()
    tenant_id = config.get_sandbox_tenant_id()
    custom_injections = config.get_sandbox_custom_injections()
    
    logger.info(f"Using sandbox tenant: {tenant_id if tenant_id else 'default (no tenant)'}")
    if custom_injections and custom_injections != "{}":
        logger.info("Custom injections configured from environment")
    
    # Initialize PlaywrightManager and get the active browser page
    browser_manager = PlaywrightManager()
    page = await browser_manager.get_current_page()
    
    if not page:
        error_msg = "No active browser page available for sandbox execution"
        logger.error(error_msg)
        return _format_error_result(error_msg)
    
    # Get browser context
    try:
        context = await browser_manager.get_browser_context()
        browser = context.browser
    except Exception as e:
        logger.warning(f"Could not get browser/context: {e}. They will be None in sandbox.")
        context = None
        browser = None
    
    # Resolve file path
    try:
        resolved_path = _resolve_file_path(file_path)
        logger.info(f"Resolved file path: {resolved_path}")
    except FileNotFoundError as e:
        error_msg = f"File not found: {file_path} - {str(e)}"
        logger.error(error_msg)
        return _format_error_result(error_msg)
    
    # Take screenshot before execution
    try:
        # Get proofs directory from config
        proofs_dir = os.path.join(config.get_project_source_root(), "opt", "proofs")
        os.makedirs(proofs_dir, exist_ok=True)
        
        screenshot_before = os.path.join(proofs_dir, f"sandbox_before_{int(time.time())}.png")
        await page.screenshot(path=screenshot_before)
        logger.info(f"Screenshot before execution: {screenshot_before}")
    except Exception as e:
        logger.warning(f"Could not take screenshot before execution: {e}")
        screenshot_before = None
    
    # Execute the sandbox
    start_time = time.time()
    try:
        result = await _execute_in_sandbox(
            page=page,
            browser=browser,
            context=context,
            browser_manager=browser_manager,
            file_path=resolved_path,
            timeout_seconds=timeout_seconds,
            function_name=function_name,
            function_args=function_args,
            tenant_id=tenant_id,
            custom_injections=custom_injections,
        )
        
        # Take screenshot after execution
        try:
            # Get proofs directory from config
            proofs_dir = os.path.join(config.get_project_source_root(), "opt", "proofs")
            os.makedirs(proofs_dir, exist_ok=True)
            
            screenshot_after = os.path.join(proofs_dir, f"sandbox_after_{int(time.time())}.png")
            await page.screenshot(path=screenshot_after)
            logger.info(f"Screenshot after execution: {screenshot_after}")
            result['screenshots'] = {
                'before': screenshot_before,
                'after': screenshot_after
            }
        except Exception as e:
            logger.warning(f"Could not take screenshot after execution: {e}")
        
        execution_time = time.time() - start_time
        result['execution_time'] = round(execution_time, 3)
        
        logger.info(f"Sandbox execution completed in {execution_time:.2f}s")
        return json.dumps(result, indent=2)
        
    except ExecutionTimeout:
        execution_time = time.time() - start_time
        error_msg = f"Execution timed out after {timeout_seconds}s"
        logger.error(error_msg)
        return _format_error_result(error_msg, execution_time=execution_time)
    
    except Exception as e:
        execution_time = time.time() - start_time
        error_msg = f"Sandbox execution failed: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        return _format_error_result(error_msg, execution_time=execution_time)


def _format_error_result(error_msg: str, execution_time: float = 0.0) -> str:
    """Format error result as JSON string."""
    return json.dumps({
        "success": False,
        "error": error_msg,
        "execution_time": round(execution_time, 3)
    }, indent=2)


def _format_success_result(result: Any, stdout: str = "", execution_time: float = 0.0) -> Dict[str, Any]:
    """Format success result."""
    return {
        "success": True,
        "result": result,
        "stdout": stdout,
        "execution_time": round(execution_time, 3)
    }


def _get_base_injections() -> Dict[str, Any]:
    """
    Get base injections available to all sandbox executions.
    These are always safe and universally useful.
    """
    import re
    import datetime
    import pathlib
    import uuid
    
    return {
        # Standard library (always safe)
        're': re,
        'datetime': datetime,
        'pathlib': pathlib,
        'uuid': uuid,
    }


def _get_tenant_specific_injections(tenant_id: str, config: Any) -> Dict[str, Any]:
    """
    Get tenant-specific injections based on tenant_id.
    Allows multi-tenant sandbox with different capabilities per tenant.
    
    Args:
        tenant_id: Identifier for the tenant/caller
        config: Global configuration
        
    Returns:
        Dictionary of tenant-specific modules/objects to inject
    """
    injections = {}
    
    # Define tenant-specific injection policies
    tenant_policies = {
        "executor_agent": {
            # Full access to automation tools
            "requests": True,
            "pandas": True,
            "numpy": True,
            "bs4": True,
        },
        "data_agent": {
            # Data processing only
            "pandas": True,
            "numpy": True,
            "requests": False,
        },
        "api_agent": {
            # API interactions only
            "requests": True,
            "httpx": True,
        },
        "restricted_agent": {
            # Minimal access
        },
    }
    
    # Get policy for this tenant (default to restricted)
    policy = tenant_policies.get(tenant_id, {})
    
    # Load allowed modules based on policy
    for module_name, allowed in policy.items():
        if allowed:
            try:
                if module_name == "bs4":
                    # Special handling for beautifulsoup4
                    injections['BeautifulSoup'] = __import__('bs4', fromlist=['BeautifulSoup']).BeautifulSoup
                else:
                    injections[module_name] = __import__(module_name)
                logger.debug(f"Injected {module_name} for tenant {tenant_id}")
            except ImportError:
                logger.warning(f"Module {module_name} not available for tenant {tenant_id}")
    
    # Add tenant-specific utilities from project
    try:
        if tenant_id == "executor_agent":
            from testzeus_hercules import utils
            injections['hercules_utils'] = utils
    except ImportError:
        pass
    
    return injections


def _get_config_driven_injections(config: Any) -> Dict[str, Any]:
    """
    Get injections defined in configuration.
    Allows dynamic configuration of available modules.
    
    Args:
        config: Global configuration
        
    Returns:
        Dictionary of configured modules to inject
    """
    injections = {}
    
    # Read from config: SANDBOX_PACKAGES="requests,pandas,numpy"
    sandbox_packages = config.get_config().get("SANDBOX_PACKAGES", "").split(",")
    
    for package_name in sandbox_packages:
        package_name = package_name.strip()
        if package_name:
            try:
                injections[package_name] = __import__(package_name)
                logger.info(f"Config-injected package: {package_name}")
            except ImportError:
                logger.warning(f"Could not import configured package: {package_name}")
    
    return injections


def _parse_custom_injections(custom_injections_json: str) -> Dict[str, Any]:
    """
    Parse and load custom injections from JSON string.
    Allows per-call injection customization.
    
    Args:
        custom_injections_json: JSON string with injection specifications
        
    Returns:
        Dictionary of custom modules/objects to inject
        
    Example JSON:
        {
            "modules": ["requests", "pandas"],
            "custom_objects": {
                "my_constant": 42,
                "my_config": {"key": "value"}
            }
        }
    """
    import json
    
    injections: Dict[str, Any] = {}
    
    if not custom_injections_json or custom_injections_json == "{}":
        return injections
    
    try:
        custom_spec = json.loads(custom_injections_json)
        
        # Handle module list
        modules = custom_spec.get("modules", [])
        for module_name in modules:
            try:
                injections[module_name] = __import__(module_name)
                logger.debug(f"Custom-injected module: {module_name}")
            except ImportError:
                logger.warning(f"Could not import custom module: {module_name}")
        
        # Handle custom objects (constants, configs, etc.)
        custom_objects = custom_spec.get("custom_objects", {})
        injections.update(custom_objects)
        
        # Handle custom Python code (advanced - be careful!)
        if "python_code" in custom_spec:
            # Execute custom setup code
            setup_code = custom_spec["python_code"]
            setup_globals: Dict[str, Any] = {}
            exec(setup_code, setup_globals)  # noqa: S102
            # Extract non-builtin items
            for key, value in setup_globals.items():
                if not key.startswith('__'):
                    injections[key] = value
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid custom_injections JSON: {e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error processing custom injections: {e}")
    
    return injections


def _build_sandbox_injections(
    config: Any,
    tenant_id: str = "",
    custom_injections_json: str = "{}",
) -> Dict[str, Any]:
    """
    Build complete set of sandbox injections from multiple sources.
    Merges injections in priority order: base -> config -> tenant -> custom
    
    Args:
        config: Global configuration
        tenant_id: Tenant/caller identifier
        custom_injections_json: JSON string with custom injections
        
    Returns:
        Complete dictionary of all injections
    """
    # Start with base (always available)
    injections = _get_base_injections()
    
    # Add config-driven injections
    injections.update(_get_config_driven_injections(config))
    
    # Add tenant-specific injections
    if tenant_id:
        injections.update(_get_tenant_specific_injections(tenant_id, config))
    
    # Add custom per-call injections (highest priority)
    injections.update(_parse_custom_injections(custom_injections_json))
    
    logger.info(f"Built sandbox with {len(injections)} injected modules/objects")
    logger.debug(f"Injected keys: {list(injections.keys())}")
    
    return injections


def _resolve_file_path(file_path: str) -> str:
    """
    Resolve the file path to an absolute path.
    
    Args:
        file_path: Original file path (absolute or relative)
        
    Returns:
        Absolute path to the file
        
    Raises:
        FileNotFoundError: If file doesn't exist
    """
    path = Path(file_path)
    
    # If already absolute and exists
    if path.is_absolute():
        if path.exists():
            return str(path)
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Try relative to project root
    config = get_global_conf()
    project_root = Path(config.get_project_source_root())
    full_path = project_root / path
    
    if full_path.exists():
        return str(full_path)
    
    # Try relative to current working directory
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return str(cwd_path)
    
    raise FileNotFoundError(f"File not found in project root or cwd: {file_path}")


async def _execute_in_sandbox(
    page: Any,
    browser: Any,
    context: Any,
    browser_manager: PlaywrightManager,
    file_path: str,
    timeout_seconds: float,
    function_name: str = "",
    function_args: str = "{}",
    tenant_id: str = "",
    custom_injections: str = "{}",
) -> Dict[str, Any]:
    """
    Execute Python code in a controlled sandbox environment.
    
    Args:
        page: Playwright page instance
        browser: Playwright browser instance (may be None)
        context: Playwright browser context instance (may be None)
        browser_manager: PlaywrightManager instance
        file_path: Absolute path to Python file
        timeout_seconds: Execution timeout
        function_name: Optional function to call
        function_args: Optional function arguments
        tenant_id: Tenant identifier from configuration for tenant-specific injections
        custom_injections: Optional JSON string with custom injections
        
    Returns:
        Dictionary with execution results
    """
    # Read the Python code
    with open(file_path, 'r', encoding='utf-8') as f:
        code = f.read()
    
    # Parse function arguments
    try:
        args_dict = json.loads(function_args) if function_args else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid function_args JSON: {e}")
    
    # Capture stdout
    import io
    stdout_capture = io.StringIO()
    
    # Prepare sandbox globals with injected modules and objects
    sandbox_globals: Dict[str, Any] = {
        # Core components (always available)
        '__builtins__': __builtins__,
        '__name__': '__sandbox__',
        '__file__': file_path,
        
        # Playwright components
        'page': page,
        'browser': browser,
        'context': context,
        'playwright_manager': browser_manager,
        
        # Async support
        'asyncio': asyncio,
        
        # Logging
        'logger': logger,
        
        # Configuration
        'config': get_global_conf(),
        
        # Basic utilities (always available)
        'os': os,
        'sys': sys,
        'json': json,
        'time': time,
        
        # Result container
        '_sandbox_result': None,
    }
    
    # Merge multi-tenant injections
    additional_injections = _build_sandbox_injections(
        config=get_global_conf(),
        tenant_id=tenant_id,
        custom_injections_json=custom_injections,
    )
    sandbox_globals.update(additional_injections)
    
    # Prepare sandbox locals
    sandbox_locals: Dict[str, Any] = {}
    
    # Execute with timeout
    async def execute_with_timeout():
        # Redirect stdout
        old_stdout = sys.stdout
        sys.stdout = stdout_capture
        
        try:
            # Execute the code
            exec(code, sandbox_globals, sandbox_locals)
            
            # Determine what to return
            result = None
            
            if function_name:
                # Call specific function
                if function_name not in sandbox_locals and function_name not in sandbox_globals:
                    raise ValueError(f"Function '{function_name}' not found in script")
                
                func = sandbox_locals.get(function_name) or sandbox_globals.get(function_name)
                
                # Call the function (handle both sync and async)
                if asyncio.iscoroutinefunction(func):
                    result = await func(**args_dict)
                else:
                    result = func(**args_dict)
            
            elif 'main' in sandbox_locals or 'main' in sandbox_globals:
                # Call main function if it exists
                main_func = sandbox_locals.get('main') or sandbox_globals.get('main')
                if asyncio.iscoroutinefunction(main_func):
                    result = await main_func()
                else:
                    result = main_func()
            
            elif '_sandbox_result' in sandbox_locals:
                # Use explicitly set result
                result = sandbox_locals['_sandbox_result']
            
            elif '_sandbox_result' in sandbox_globals:
                result = sandbox_globals['_sandbox_result']
            
            else:
                # Return the last expression or None
                result = None
            
            return result
            
        finally:
            # Restore stdout
            sys.stdout = old_stdout
    
    # Run with timeout
    try:
        result = await asyncio.wait_for(
            execute_with_timeout(),
            timeout=timeout_seconds
        )
        
        stdout_output = stdout_capture.getvalue()
        
        return _format_success_result(result, stdout_output)
        
    except asyncio.TimeoutError:
        raise ExecutionTimeout(f"Execution exceeded {timeout_seconds} seconds")
    
    except Exception as e:
        error_msg = f"Execution error: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise

