#!/usr/bin/env python3
"""
Verify that the page content tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_page_content_tool_logging():
    """Verify page content tool logging integration."""
    print("=== Verifying Page Content Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import get_page_text, get_interactive_elements
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
                    <title>Test Page for Content Extraction</title>
                </head>
                <body>
                    <h1>Main Heading</h1>
                    <p>This is a paragraph with some text content for testing.</p>
                    
                    <div class="content-section">
                        <h2>Interactive Elements Section</h2>
                        <button id="btn1" md="button-md-001">Click Me</button>
                        <input type="text" id="input1" md="input-md-002" placeholder="Enter text"/>
                        <select id="select1" md="select-md-003">
                            <option value="option1">Option 1</option>
                            <option value="option2">Option 2</option>
                        </select>
                        <a href="#" id="link1" md="link-md-004">Test Link</a>
                        <textarea id="textarea1" md="textarea-md-005" placeholder="Enter description"></textarea>
                    </div>
                    
                    <div class="info-section">
                        <p>Additional content for text extraction testing.</p>
                        <ul>
                            <li>List item 1</li>
                            <li>List item 2</li>
                            <li>List item 3</li>
                        </ul>
                    </div>
                    
                    <script>
                        // This script content should be filtered out
                        console.log("This should not appear in text content");
                    </script>
                    
                    <style>
                        /* This style content should be filtered out */
                        body { margin: 0; }
                    </style>
                </body>
                </html>
            """)
            
            print("   Testing get_page_text function...")
            result1 = await get_page_text(
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Get page text result: {result1.get('success', False)}")
            if result1.get('success'):
                text_length = len(result1.get('text_content', ''))
                print(f"   ✓ Text content length: {text_length} characters")
            
            print("   Testing get_interactive_elements function...")
            result2 = await get_interactive_elements(
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Get interactive elements result: {result2.get('success', False)}")
            if result2.get('success'):
                element_count = result2.get('element_count', 0)
                print(f"   ✓ Interactive elements found: {element_count}")
            
            await page.set_content("<html><body></body></html>")
            
            print("   Testing get_page_text on minimal page...")
            result3 = await get_page_text(
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Minimal page text result: {result3.get('success', False)}")
            
            print("   Testing get_interactive_elements on minimal page...")
            result4 = await get_interactive_elements(
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Minimal page elements result: {result4.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 4:
                print("   ✓ All page content scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        if 'text_length' in interaction.additional_data:
                            text_len = interaction.additional_data.get('text_length', 0)
                            print(f"        Text length: {text_len}")
                        if 'element_count' in interaction.additional_data:
                            elem_count = interaction.additional_data.get('element_count', 0)
                            print(f"        Element count: {elem_count}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if ("get_page_text" in generated_code or "get_interactive_elements" in generated_code) and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes page content functions and proper imports")
                        
                        expected_functions = ["get_page_text", "get_interactive_elements"]
                        functions_found = sum(1 for func in expected_functions if func in generated_code)
                        
                        print(f"   ✓ Page content functions found in code: {functions_found}/{len(expected_functions)}")
                        
                        if functions_found >= 1:
                            return True
                        else:
                            print("   ❌ Generated code missing expected page content functions")
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
        print(f"❌ Page content tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying page content tool logging integration...")
    
    result = await verify_page_content_tool_logging()
    
    if result:
        print("\n✅ Page content tool logging verification passed!")
        return 0
    else:
        print("\n❌ Page content tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
