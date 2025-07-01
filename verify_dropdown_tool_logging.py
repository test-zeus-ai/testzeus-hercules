#!/usr/bin/env python3
"""
Verify that the dropdown tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_dropdown_tool_logging():
    """Verify dropdown tool logging integration."""
    print("=== Verifying Dropdown Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import select_dropdown
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            await playwright_manager.initialize()
            
            page = await playwright_manager.get_page()
            await page.set_content("""
                <html>
                <body>
                    <select id="country" md="country-md-123">
                        <option value="">Select Country</option>
                        <option value="us">United States</option>
                        <option value="ca">Canada</option>
                        <option value="uk">United Kingdom</option>
                        <option value="de">Germany</option>
                    </select>
                    
                    <select id="language" md="language-md-456">
                        <option value="en">English</option>
                        <option value="es">Spanish</option>
                        <option value="fr">French</option>
                        <option value="de">German</option>
                    </select>
                    
                    <select id="priority" md="priority-md-789">
                        <option value="low">Low Priority</option>
                        <option value="medium">Medium Priority</option>
                        <option value="high">High Priority</option>
                    </select>
                </body>
                </html>
            """)
            
            print("   Testing successful dropdown selection by value...")
            result1 = await select_dropdown(
                selector="[md='country-md-123']",
                value="us",
                by="value",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Successful dropdown selection result: {result1.get('success', False)}")
            
            print("   Testing dropdown selection by text...")
            result2 = await select_dropdown(
                selector="[md='language-md-456']",
                value="Spanish",
                by="text",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Text-based selection result: {result2.get('success', False)}")
            
            print("   Testing dropdown selection by index...")
            result3 = await select_dropdown(
                selector="[md='priority-md-789']",
                value=2,
                by="index",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Index-based selection result: {result3.get('success', False)}")
            
            print("   Testing failed dropdown selection (element not found)...")
            result4 = await select_dropdown(
                selector="[md='nonexistent-md-999']",
                value="test",
                by="value",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Failed selection result (expected): {result4.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 4:
                print("   ✓ All dropdown scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        value = interaction.additional_data.get('value', 'N/A')
                        by_method = interaction.additional_data.get('by', 'N/A')
                        print(f"        Value: '{value}', Method: {by_method}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "select_dropdown" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes select_dropdown and proper imports")
                        
                        expected_values = ["us", "Spanish", "2"]
                        expected_methods = ["value", "text", "index"]
                        
                        values_found = sum(1 for val in expected_values if str(val) in generated_code)
                        methods_found = sum(1 for method in expected_methods if f"by='{method}'" in generated_code)
                        
                        print(f"   ✓ Dropdown values found in code: {values_found}/{len(expected_values)}")
                        print(f"   ✓ Selection methods found in code: {methods_found}/{len(expected_methods)}")
                        
                        if values_found >= 2 and methods_found >= 2:
                            return True
                        else:
                            print("   ❌ Generated code missing expected dropdown content")
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
        print(f"❌ Dropdown tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying dropdown tool logging integration...")
    
    result = await verify_dropdown_tool_logging()
    
    if result:
        print("\n✅ Dropdown tool logging verification passed!")
        return 0
    else:
        print("\n❌ Dropdown tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
