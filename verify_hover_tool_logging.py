#!/usr/bin/env python3
"""
Verify that the hover tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_hover_tool_logging():
    """Verify hover tool logging integration."""
    print("=== Verifying Hover Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import hover_element
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
                <head>
                    <style>
                        .hover-target { 
                            padding: 20px; 
                            background: lightblue; 
                            margin: 10px;
                            transition: background 0.3s;
                        }
                        .hover-target:hover { 
                            background: lightgreen; 
                        }
                        .hidden { 
                            display: none; 
                        }
                    </style>
                </head>
                <body>
                    <div class="hover-target" id="button1" md="hover-md-123">Hover Me 1</div>
                    <div class="hover-target" id="button2" md="hover-md-456">Hover Me 2</div>
                    <div class="hidden" id="hidden" md="hidden-md-789">Hidden Element</div>
                </body>
                </html>
            """)
            
            print("   Testing successful hover...")
            result1 = await hover_element(
                selector="[md='hover-md-123']",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Successful hover result: {result1.get('success', False)}")
            
            print("   Testing second successful hover...")
            result2 = await hover_element(
                selector="[md='hover-md-456']",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Second hover result: {result2.get('success', False)}")
            
            print("   Testing failed hover (element not found)...")
            result3 = await hover_element(
                selector="[md='nonexistent-md-999']",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Failed hover result (expected): {result3.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 3:
                print("   ✓ All hover scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.element_info:
                        print(f"        Element: {interaction.element_info.get('tag_name', 'unknown')}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "hover_element" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes hover_element and proper imports")
                        
                        if "hover-md-123" in generated_code and "hover-md-456" in generated_code:
                            print("   ✓ Generated code includes hover selectors")
                            return True
                        else:
                            print("   ❌ Generated code missing expected hover selectors")
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
        print(f"❌ Hover tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying hover tool logging integration...")
    
    result = await verify_hover_tool_logging()
    
    if result:
        print("\n✅ Hover tool logging verification passed!")
        return 0
    else:
        print("\n❌ Hover tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
