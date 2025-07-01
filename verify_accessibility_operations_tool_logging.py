#!/usr/bin/env python3
"""
Verify that the accessibility operations tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_accessibility_operations_tool_logging():
    """Verify accessibility operations tool logging integration."""
    print("=== Verifying Accessibility Operations Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import test_page_accessibility
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            await playwright_manager.initialize()
            
            print("   Testing accessibility on page with violations...")
            page = await playwright_manager.get_page()
            await page.set_content("""
                <html>
                <head><title>Test Page</title></head>
                <body>
                    <h1>Test Page</h1>
                    <img src="test.jpg">
                    <button>Click me</button>
                    <input type="text">
                    <div style="color: #ccc; background-color: #ddd;">Low contrast text</div>
                </body>
                </html>
            """)
            
            result1 = await test_page_accessibility(
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Accessibility test result: {result1.get('success', False)}")
            violations_count = result1.get('violations_count', 0)
            passes_count = result1.get('passes_count', 0)
            print(f"   ✓ Violations: {violations_count}, Passes: {passes_count}")
            
            print("   Testing accessibility on improved page...")
            await page.set_content("""
                <html lang="en">
                <head><title>Accessible Test Page</title></head>
                <body>
                    <h1>Accessible Test Page</h1>
                    <img src="test.jpg" alt="Test image">
                    <button aria-label="Click this button">Click me</button>
                    <label for="text-input">Enter text:</label>
                    <input type="text" id="text-input">
                    <div style="color: #000; background-color: #fff;">High contrast text</div>
                </body>
                </html>
            """)
            
            result2 = await test_page_accessibility(
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Improved accessibility test result: {result2.get('success', False)}")
            violations_count2 = result2.get('violations_count', 0)
            passes_count2 = result2.get('passes_count', 0)
            print(f"   ✓ Violations: {violations_count2}, Passes: {passes_count2}")
            
            print("   Testing accessibility with URL navigation...")
            result3 = await test_page_accessibility(
                page_url="data:text/html,<html><head><title>Simple</title></head><body><h1>Simple Page</h1><p>This is a simple accessible page.</p></body></html>",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ URL accessibility test result: {result3.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 3:
                print("   ✓ All accessibility operations scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        violations = interaction.additional_data.get('violations_count', 'N/A')
                        passes = interaction.additional_data.get('passes_count', 'N/A')
                        incomplete = interaction.additional_data.get('incomplete_count', 'N/A')
                        has_violations = interaction.additional_data.get('has_violations', 'N/A')
                        print(f"        Violations: {violations}, Passes: {passes}, Incomplete: {incomplete}, Has Issues: {has_violations}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "test_page_accessibility" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes test_page_accessibility and proper imports")
                        
                        expected_accessibility_content = ["accessibility", "page_url", "violations", "test"]
                        accessibility_content_found = sum(1 for content in expected_accessibility_content if content in generated_code)
                        
                        print(f"   ✓ Accessibility content found in code: {accessibility_content_found}/{len(expected_accessibility_content)}")
                        
                        if accessibility_content_found >= 3:
                            return True
                        else:
                            print("   ❌ Generated code missing expected accessibility content")
                            return False
                    else:
                        print("   ❌ Generated code missing expected content")
                        return False
                else:
                    print("   ❌ Code generation failed or too short")
                    return False
            else:
                print("   ❌ Not enough interactions logged")
                return False
                
        finally:
            await playwright_manager.close()
        
    except Exception as e:
        print(f"❌ Accessibility operations tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying accessibility operations tool logging integration...")
    
    result = await verify_accessibility_operations_tool_logging()
    
    if result:
        print("\n✅ Accessibility operations tool logging verification passed!")
        return 0
    else:
        print("\n❌ Accessibility operations tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
