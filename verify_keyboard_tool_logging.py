#!/usr/bin/env python3
"""
Verify that the keyboard tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_keyboard_tool_logging():
    """Verify keyboard tool logging integration."""
    print("=== Verifying Keyboard Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import press_key_combination
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
                    <input type="text" id="test-input" placeholder="Type here to test keyboard"/>
                    <textarea id="test-textarea" placeholder="Test area for keyboard input"></textarea>
                    <div id="output"></div>
                    <script>
                        document.addEventListener('keydown', function(e) {
                            const output = document.getElementById('output');
                            output.innerHTML += 'Key pressed: ' + e.key + '<br>';
                        });
                    </script>
                </body>
                </html>
            """)
            
            await page.focus('#test-input')
            
            print("   Testing single key press...")
            result1 = await press_key_combination(
                key_combination="Enter",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Single key press result: {result1.get('success', False)}")
            
            print("   Testing key combination (Ctrl+A)...")
            result2 = await press_key_combination(
                key_combination="Control+a",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Key combination result: {result2.get('success', False)}")
            
            print("   Testing complex key combination (Ctrl+Shift+Z)...")
            result3 = await press_key_combination(
                key_combination="Control+Shift+z",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Complex combination result: {result3.get('success', False)}")
            
            print("   Testing arrow key...")
            result4 = await press_key_combination(
                key_combination="ArrowDown",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Arrow key result: {result4.get('success', False)}")
            
            print("   Testing function key...")
            result5 = await press_key_combination(
                key_combination="F5",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Function key result: {result5.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 5:
                print("   ✓ All keyboard scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        keys = interaction.additional_data.get('keys', [])
                        print(f"        Keys: {keys}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "press_key_combination" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes press_key_combination and proper imports")
                        
                        expected_keys = ["Enter", "Control+a", "Control+Shift+z", "ArrowDown", "F5"]
                        keys_found = sum(1 for key in expected_keys if key in generated_code)
                        
                        print(f"   ✓ Key combinations found in code: {keys_found}/{len(expected_keys)}")
                        
                        if keys_found >= 4:
                            return True
                        else:
                            print("   ❌ Generated code missing expected key combinations")
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
        print(f"❌ Keyboard tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying keyboard tool logging integration...")
    
    result = await verify_keyboard_tool_logging()
    
    if result:
        print("\n✅ Keyboard tool logging verification passed!")
        return 0
    else:
        print("\n❌ Keyboard tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
