#!/usr/bin/env python3
"""
Verify that the click tool's interactions are properly converted to code.
"""

import asyncio
import sys
import os
import tempfile

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_click_code_generation():
    """Verify click tool code generation."""
    print("=== Verifying Click Tool Code Generation ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import click_element, CodeGenerator
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        code_generator = CodeGenerator(config)
        
        logger.clear_interactions()
        
        try:
            await playwright_manager.initialize()
            
            page = await playwright_manager.get_page()
            await page.set_content("""
                <html>
                <body>
                    <button id="btn1" md="button-md-001">Regular Click</button>
                    <button id="btn2" md="button-md-002">Right Click</button>
                    <button id="btn3" md="button-md-003">Double Click</button>
                </body>
                </html>
            """)
            
            print("   Performing various click operations for code generation...")
            
            await click_element(
                selector="[md='button-md-001']",
                click_type="click",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await click_element(
                selector="[md='button-md-002']",
                click_type="right_click",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await click_element(
                selector="[md='button-md-003']",
                click_type="double_click",
                config=config,
                playwright_manager=playwright_manager
            )
            
            interactions = logger.get_successful_interactions()
            print(f"   ✓ Successful interactions logged: {len(interactions)}")
            
            if len(interactions) >= 3:
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - {interaction.additional_data}")
                
                generated_code = code_generator.generate_from_logger(logger)
                print(f"   ✓ Generated code length: {len(generated_code)} characters")
                
                if "from testzeus_hercules_tools" in generated_code:
                    print("   ✓ Generated code imports testzeus_hercules_tools")
                else:
                    print("   ❌ Generated code missing testzeus_hercules_tools import")
                    return False
                
                if "click_element" in generated_code:
                    print("   ✓ Generated code includes click_element function")
                else:
                    print("   ❌ Generated code missing click_element function")
                    return False
                
                click_types_found = []
                if "click_type='click'" in generated_code:
                    click_types_found.append("click")
                if "click_type='right_click'" in generated_code:
                    click_types_found.append("right_click")
                if "click_type='double_click'" in generated_code:
                    click_types_found.append("double_click")
                
                print(f"   ✓ Click types in generated code: {click_types_found}")
                
                try:
                    compile(generated_code, '<generated>', 'exec')
                    print("   ✓ Generated code has valid Python syntax")
                except SyntaxError as e:
                    print(f"   ❌ Generated code has syntax error: {e}")
                    return False
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                    f.write(generated_code)
                    temp_file = f.name
                
                print(f"   ✓ Generated code saved to: {temp_file}")
                
                lines = generated_code.split('\n')
                print("   ✓ Generated code preview (click operations):")
                for i, line in enumerate(lines):
                    if 'click_element' in line or 'click_type' in line:
                        print(f"     {i+1:2d}: {line}")
                
                os.unlink(temp_file)
                
                print("   Testing code mode execution simulation...")
                code_mode_config = ToolsConfig(mode='code', enable_logging=False, headless=True)
                
                if "mode='code'" in generated_code:
                    print("   ✓ Generated code uses code mode configuration")
                else:
                    print("   ❌ Generated code missing code mode configuration")
                    return False
                
                return True
            else:
                print("   ❌ Not enough successful interactions for code generation")
                return False
                
        finally:
            await playwright_manager.close()
        
    except Exception as e:
        print(f"❌ Click tool code generation verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying click tool code generation...")
    
    result = await verify_click_code_generation()
    
    if result:
        print("\n✅ Click tool code generation verification passed!")
        return 0
    else:
        print("\n❌ Click tool code generation verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
