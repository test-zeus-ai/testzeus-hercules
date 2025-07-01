#!/usr/bin/env python3
"""
Verify that the dropdown tool's interactions are properly converted to code.
"""

import asyncio
import sys
import os
import tempfile

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_dropdown_code_generation():
    """Verify dropdown tool code generation."""
    print("=== Verifying Dropdown Tool Code Generation ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import select_dropdown, CodeGenerator
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
                    <form>
                        <select id="category" md="category-md-001">
                            <option value="">Select Category</option>
                            <option value="electronics">Electronics</option>
                            <option value="clothing">Clothing</option>
                            <option value="books">Books</option>
                            <option value="home">Home & Garden</option>
                        </select>
                        
                        <select id="size" md="size-md-002">
                            <option value="xs">Extra Small</option>
                            <option value="s">Small</option>
                            <option value="m">Medium</option>
                            <option value="l">Large</option>
                            <option value="xl">Extra Large</option>
                        </select>
                        
                        <select id="priority" md="priority-md-003">
                            <option value="1">Low Priority</option>
                            <option value="2">Normal Priority</option>
                            <option value="3">High Priority</option>
                            <option value="4">Urgent Priority</option>
                        </select>
                        
                        <select id="status" md="status-md-004">
                            <option value="draft">Draft</option>
                            <option value="pending">Pending Review</option>
                            <option value="approved">Approved</option>
                            <option value="rejected">Rejected</option>
                        </select>
                    </form>
                </body>
                </html>
            """)
            
            print("   Performing various dropdown selection operations for code generation...")
            
            await select_dropdown(
                selector="[md='category-md-001']",
                value="electronics",
                by="value",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await select_dropdown(
                selector="[md='size-md-002']",
                value="Medium",
                by="text",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await select_dropdown(
                selector="[md='priority-md-003']",
                value=2,
                by="index",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await select_dropdown(
                selector="[md='status-md-004']",
                value="approved",
                by="value",
                config=config,
                playwright_manager=playwright_manager
            )
            
            interactions = logger.get_successful_interactions()
            print(f"   ✓ Successful interactions logged: {len(interactions)}")
            
            if len(interactions) >= 4:
                for i, interaction in enumerate(interactions, 1):
                    value = interaction.additional_data.get('value', 'N/A') if interaction.additional_data else 'N/A'
                    by_method = interaction.additional_data.get('by', 'N/A') if interaction.additional_data else 'N/A'
                    print(f"     {i}: {interaction.tool_name} - Value: '{value}' - Method: {by_method}")
                
                generated_code = code_generator.generate_from_logger(logger)
                print(f"   ✓ Generated code length: {len(generated_code)} characters")
                
                if "from testzeus_hercules_tools" in generated_code:
                    print("   ✓ Generated code imports testzeus_hercules_tools")
                else:
                    print("   ❌ Generated code missing testzeus_hercules_tools import")
                    return False
                
                if "select_dropdown" in generated_code:
                    print("   ✓ Generated code includes select_dropdown function")
                else:
                    print("   ❌ Generated code missing select_dropdown function")
                    return False
                
                expected_selectors = ["#category", "#size", "#priority", "#status"]
                selectors_found = []
                for selector in expected_selectors:
                    if selector in generated_code:
                        selectors_found.append(selector)
                
                print(f"   ✓ CSS selectors found in generated code: {len(selectors_found)}/{len(expected_selectors)}")
                
                expected_values = ["electronics", "Medium", "2", "approved"]
                expected_methods = ["value", "text", "index"]
                
                values_found = []
                for value in expected_values:
                    if str(value) in generated_code:
                        values_found.append(value)
                
                methods_found = []
                for method in expected_methods:
                    if f"by='{method}'" in generated_code:
                        methods_found.append(method)
                
                print(f"   ✓ Dropdown values found in generated code: {len(values_found)}/{len(expected_values)}")
                print(f"   ✓ Selection methods found in generated code: {len(methods_found)}/{len(expected_methods)}")
                
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
                print("   ✓ Generated code preview (dropdown operations):")
                for i, line in enumerate(lines):
                    if 'select_dropdown' in line or any(sel in line for sel in expected_selectors):
                        print(f"     {i+1:2d}: {line}")
                
                os.unlink(temp_file)
                
                print("   Testing code mode execution simulation...")
                
                if "mode='code'" in generated_code:
                    print("   ✓ Generated code uses code mode configuration")
                else:
                    print("   ❌ Generated code missing code mode configuration")
                    return False
                
                if len(selectors_found) >= 3 and len(values_found) >= 3 and len(methods_found) >= 2:
                    print("   ✓ Dual-mode dropdown code generation working correctly")
                    return True
                else:
                    print("   ❌ Not enough dropdown content converted properly")
                    return False
            else:
                print("   ❌ Not enough successful interactions for code generation")
                return False
                
        finally:
            await playwright_manager.close()
        
    except Exception as e:
        print(f"❌ Dropdown tool code generation verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying dropdown tool code generation...")
    
    result = await verify_dropdown_code_generation()
    
    if result:
        print("\n✅ Dropdown tool code generation verification passed!")
        return 0
    else:
        print("\n❌ Dropdown tool code generation verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
