#!/usr/bin/env python3
"""
Verify that the input tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_input_tool_logging():
    """Verify input tool logging integration."""
    print("=== Verifying Input Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import enter_text
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
                    <input type="text" id="text-input" md="input-md-123" placeholder="Enter text here"/>
                    <textarea id="textarea" md="textarea-md-456" placeholder="Enter long text"></textarea>
                    <input type="password" id="password" md="password-md-789" placeholder="Password"/>
                </body>
                </html>
            """)
            
            print("   Testing successful text input...")
            result1 = await enter_text(
                selector="[md='input-md-123']",
                text="Hello World!",
                clear_first=True,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Successful text input result: {result1.get('success', False)}")
            
            print("   Testing textarea input...")
            result2 = await enter_text(
                selector="[md='textarea-md-456']",
                text="This is a longer text for textarea testing.",
                clear_first=False,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Textarea input result: {result2.get('success', False)}")
            
            print("   Testing failed input (element not found)...")
            result3 = await enter_text(
                selector="[md='nonexistent-md-999']",
                text="This should fail",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Failed input result (expected): {result3.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 3:
                print("   ✓ All input scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        text_preview = interaction.additional_data.get('text', '')[:20]
                        clear_first = interaction.additional_data.get('clear_first', True)
                        print(f"        Text: '{text_preview}...', Clear first: {clear_first}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "enter_text" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes enter_text and proper imports")
                        
                        if "Hello World!" in generated_code and "longer text" in generated_code:
                            print("   ✓ Generated code includes input text content")
                            return True
                        else:
                            print("   ❌ Generated code missing expected text content")
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
        print(f"❌ Input tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying input tool logging integration...")
    
    result = await verify_input_tool_logging()
    
    if result:
        print("\n✅ Input tool logging verification passed!")
        return 0
    else:
        print("\n❌ Input tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
