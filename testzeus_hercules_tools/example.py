"""
Example usage of testzeus_hercules_tools in both agent and code modes.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
from testzeus_hercules_tools.tools import (
    click_element, 
    enter_text, 
    hover_element, 
    select_dropdown,
    open_url,
    press_key_combination,
    upload_file,
    get_page_text,
    get_interactive_elements,
    execute_select_query,
    http_request,
    test_page_accessibility,
    wait_for_seconds,
    wait_until_condition,
    run_security_scan,
    InteractionLogger,
    CodeGenerator
)


async def agent_mode_example():
    """Example using agent mode with md attributes."""
    print("=== Agent Mode Example ===")
    
    config = ToolsConfig(mode="agent", headless=False)
    playwright_manager = ToolsPlaywrightManager(config)
    logger = InteractionLogger(config)
    
    try:
        await playwright_manager.initialize()
        
        result = await open_url("https://example.com", config=config, playwright_manager=playwright_manager)
        print(f"Navigation result: {result['success']}")
        
        result = await get_page_text(config=config, playwright_manager=playwright_manager)
        print(f"Page text length: {len(result.get('text_content', ''))}")
        
        result = await get_interactive_elements(config=config, playwright_manager=playwright_manager)
        print(f"Found {result.get('element_count', 0)} interactive elements")
        
        
        print("Agent mode example completed successfully")
        
    finally:
        await playwright_manager.close()


async def code_mode_example():
    """Example using code mode with CSS/XPath selectors."""
    print("=== Code Mode Example ===")
    
    config = ToolsConfig(mode="code", headless=False)
    playwright_manager = ToolsPlaywrightManager(config)
    logger = InteractionLogger(config)
    
    try:
        await playwright_manager.initialize()
        
        result = await open_url("https://httpbin.org/forms/post", config=config, playwright_manager=playwright_manager)
        print(f"Navigation result: {result['success']}")
        
        result = await get_page_text(config=config, playwright_manager=playwright_manager)
        print(f"Page text length: {len(result.get('text_content', ''))}")
        
        result = await get_interactive_elements(config=config, playwright_manager=playwright_manager)
        print(f"Found {result.get('element_count', 0)} interactive elements")
        
        
        print("Code mode example completed successfully")
        
    finally:
        await playwright_manager.close()


async def specialized_tools_agent_mode_example():
    """Example using specialized tools in agent mode."""
    print("=== Specialized Tools Agent Mode Example ===")
    
    config = ToolsConfig(mode="agent", headless=False)
    playwright_manager = ToolsPlaywrightManager(config)
    
    try:
        await playwright_manager.initialize()
        
        print("Testing time operations...")
        result = await wait_for_seconds(2.0, reason="Demo wait", config=config, playwright_manager=playwright_manager)
        print(f"Wait result: {result['success']} - Duration: {result.get('actual_duration', 0):.2f}s")
        
        print("Testing API operations...")
        result = await http_request(
            method="GET",
            url="https://httpbin.org/get",
            query_params={"test": "value"},
            config=config,
            playwright_manager=playwright_manager
        )
        print(f"API result: {result['success']} - Status: {result.get('status_code', 'N/A')}")
        
        print("Testing accessibility operations...")
        result = await test_page_accessibility(
            page_url="https://example.com",
            config=config,
            playwright_manager=playwright_manager
        )
        print(f"Accessibility result: {result['success']} - Violations: {result.get('violations_count', 0)}")
        
        print("Testing security operations...")
        result = await run_security_scan(
            target_url="https://example.com",
            scan_type="xss",
            config=config,
            playwright_manager=playwright_manager
        )
        print(f"Security scan result: {result['success']} - Vulnerabilities: {result.get('vulnerabilities_found', 0)}")
        
        print("Specialized tools agent mode example completed successfully")
        
    except Exception as e:
        print(f"Error in specialized tools example: {str(e)}")
        
    finally:
        await playwright_manager.close()


async def specialized_tools_code_mode_example():
    """Example using specialized tools in code mode."""
    print("=== Specialized Tools Code Mode Example ===")
    
    config = ToolsConfig(mode="code", headless=False)
    playwright_manager = ToolsPlaywrightManager(config)
    
    try:
        await playwright_manager.initialize()
        
        print("Testing time operations in code mode...")
        result = await wait_for_seconds(1.5, reason="Code mode demo", config=config, playwright_manager=playwright_manager)
        print(f"Wait result: {result['success']} - Duration: {result.get('actual_duration', 0):.2f}s")
        
        print("Testing API operations with auth in code mode...")
        result = await http_request(
            method="POST",
            url="https://httpbin.org/post",
            body={"message": "Hello from code mode"},
            body_mode="json",
            config=config,
            playwright_manager=playwright_manager
        )
        print(f"API result: {result['success']} - Status: {result.get('status_code', 'N/A')}")
        
        print("Testing conditional wait...")
        await open_url("https://example.com", config=config, playwright_manager=playwright_manager)
        result = await wait_until_condition(
            condition_check="document.readyState === 'complete'",
            max_wait_seconds=10.0,
            config=config,
            playwright_manager=playwright_manager
        )
        print(f"Condition wait result: {result['success']} - Elapsed: {result.get('elapsed_seconds', 0):.2f}s")
        
        print("Testing security operations in code mode...")
        result = await run_security_scan(
            target_url="https://example.com",
            scan_type="cve",
            config=config,
            playwright_manager=playwright_manager
        )
        print(f"Security scan result: {result['success']} - Vulnerabilities: {result.get('vulnerabilities_found', 0)}")
        
        print("Specialized tools code mode example completed successfully")
        
    except Exception as e:
        print(f"Error in specialized tools code mode example: {str(e)}")
        
    finally:
        await playwright_manager.close()


async def logging_and_code_generation_example():
    """Example showing interaction logging and code generation."""
    print("=== Logging and Code Generation Example ===")
    
    config = ToolsConfig(mode="agent", enable_logging=True)
    playwright_manager = ToolsPlaywrightManager(config)
    logger = InteractionLogger(config)
    code_generator = CodeGenerator(config)
    
    try:
        await playwright_manager.initialize()
        
        print("Simulating mixed browser and specialized tool interactions...")
        
        await open_url("https://example.com", config=config, playwright_manager=playwright_manager)
        
        await wait_for_seconds(1.0, reason="Demo interaction", config=config, playwright_manager=playwright_manager)
        await http_request("GET", "https://httpbin.org/get", config=config, playwright_manager=playwright_manager)
        await test_page_accessibility(page_url="https://example.com", config=config, playwright_manager=playwright_manager)
        
        interactions = logger.get_successful_interactions()
        if interactions:
            generated_code = code_generator.generate_code_from_logs(interactions)
            print("Generated code preview:")
            print(generated_code[:800] + "..." if len(generated_code) > 800 else generated_code)
            
            filepath = code_generator.save_generated_code(generated_code, "mixed_tools_example.py")
            print(f"Complete code saved to: {filepath}")
        else:
            print("No successful interactions to generate code from")
        
    except Exception as e:
        print(f"Error in logging example: {str(e)}")
        
    finally:
        await playwright_manager.close()


async def main():
    """Run all examples."""
    await agent_mode_example()
    print()
    await code_mode_example()
    print()
    await specialized_tools_agent_mode_example()
    print()
    await specialized_tools_code_mode_example()
    print()
    await logging_and_code_generation_example()


if __name__ == "__main__":
    asyncio.run(main())
