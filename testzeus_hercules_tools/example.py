"""
Example usage of testzeus_hercules_tools in both agent and code modes.
"""

import asyncio
from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
from testzeus_hercules_tools.tools import (
    click_element, 
    enter_text, 
    hover_element, 
    select_dropdown,
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
        page = await playwright_manager.get_page()
        
        await page.goto("https://example.com")
        
        
        print("Agent mode initialized successfully")
        
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
        page = await playwright_manager.get_page()
        
        await page.goto("https://example.com")
        
        
        print("Code mode initialized successfully")
        
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
        page = await playwright_manager.get_page()
        await page.goto("https://example.com")
        
        print("Simulating interactions...")
        
        interactions = logger.get_successful_interactions()
        if interactions:
            generated_code = code_generator.generate_code_from_logs(interactions)
            print("Generated code:")
            print(generated_code[:500] + "..." if len(generated_code) > 500 else generated_code)
            
            filepath = code_generator.save_generated_code(generated_code)
            print(f"Code saved to: {filepath}")
        else:
            print("No successful interactions to generate code from")
        
    finally:
        await playwright_manager.close()


async def main():
    """Run all examples."""
    await agent_mode_example()
    print()
    await code_mode_example()
    print()
    await logging_and_code_generation_example()


if __name__ == "__main__":
    asyncio.run(main())
