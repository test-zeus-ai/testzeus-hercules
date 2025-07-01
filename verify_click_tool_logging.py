#!/usr/bin/env python3
"""
Verify that the click tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_click_tool_logging():
    """Verify click tool logging integration."""
    print("=== Verifying Click Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import click_element
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
                    <button id="test-button" md="test-md-123">Click Me</button>
                    <div id="hidden" style="display:none;" md="hidden-md-456">Hidden</div>
                </body>
                </html>
            """)
            
            print("   Testing successful click...")
            result1 = await click_element(
                selector="[md='test-md-123']",
                click_type="click",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Successful click result: {result1.get('success', False)}")
            
            print("   Testing failed click (element not found)...")
            result2 = await click_element(
                selector="[md='nonexistent-md-999']",
                click_type="click",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Failed click result (expected): {result2.get('success', False)}")
            
            print("   Testing failed click (element not visible)...")
            result3 = await click_element(
                selector="[md='hidden-md-456']",
                click_type="right_click",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Hidden element click result (expected fail): {result3.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 3:
                print("   ✓ All click scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        print(f"        Additional data: {interaction.additional_data}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "click_element" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes click_element and proper imports")
                        return True
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
        print(f"❌ Click tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying click tool logging integration...")
    
    result = await verify_click_tool_logging()
    
    if result:
        print("\n✅ Click tool logging verification passed!")
        return 0
    else:
        print("\n❌ Click tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
