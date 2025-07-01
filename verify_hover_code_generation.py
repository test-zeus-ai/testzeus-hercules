#!/usr/bin/env python3
"""
Verify that the hover tool's interactions are properly converted to code.
"""

import asyncio
import sys
import os
import tempfile

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_hover_code_generation():
    """Verify hover tool code generation."""
    print("=== Verifying Hover Tool Code Generation ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import hover_element, CodeGenerator
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
                <head>
                    <style>
                        .hover-item { 
                            padding: 15px; 
                            margin: 10px; 
                            background: lightblue; 
                            transition: all 0.3s;
                        }
                        .hover-item:hover { 
                            background: lightgreen; 
                            transform: scale(1.05);
                        }
                    </style>
                </head>
                <body>
                    <div class="hover-item" id="nav-menu" md="nav-menu-md-001">Navigation Menu</div>
                    <button class="hover-item" id="submit-btn" md="submit-btn-md-002">Submit Button</button>
                    <a href="#" class="hover-item" id="help-link" md="help-link-md-003">Help Link</a>
                    <span class="hover-item" id="tooltip-trigger" md="tooltip-md-004">Tooltip Trigger</span>
                </body>
                </html>
            """)
            
            print("   Performing various hover operations for code generation...")
            
            await hover_element(
                selector="[md='nav-menu-md-001']",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await hover_element(
                selector="[md='submit-btn-md-002']",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await hover_element(
                selector="[md='help-link-md-003']",
                config=config,
                playwright_manager=playwright_manager
            )
            
            await hover_element(
                selector="[md='tooltip-md-004']",
                config=config,
                playwright_manager=playwright_manager
            )
            
            interactions = logger.get_successful_interactions()
            print(f"   ✓ Successful interactions logged: {len(interactions)}")
            
            if len(interactions) >= 4:
                for i, interaction in enumerate(interactions, 1):
                    element_tag = interaction.element_info.get('tag_name', 'unknown') if interaction.element_info else 'unknown'
                    element_id = interaction.element_info.get('attributes', {}).get('id', 'no-id') if interaction.element_info else 'no-id'
                    print(f"     {i}: {interaction.tool_name} - {element_tag}#{element_id}")
                
                generated_code = code_generator.generate_from_logger(logger)
                print(f"   ✓ Generated code length: {len(generated_code)} characters")
                
                if "from testzeus_hercules_tools" in generated_code:
                    print("   ✓ Generated code imports testzeus_hercules_tools")
                else:
                    print("   ❌ Generated code missing testzeus_hercules_tools import")
                    return False
                
                if "hover_element" in generated_code:
                    print("   ✓ Generated code includes hover_element function")
                else:
                    print("   ❌ Generated code missing hover_element function")
                    return False
                
                expected_selectors = ["#nav-menu", "#submit-btn", "#help-link", "#tooltip-trigger"]
                selectors_found = []
                for selector in expected_selectors:
                    if selector in generated_code:
                        selectors_found.append(selector)
                
                print(f"   ✓ CSS selectors found in generated code: {len(selectors_found)}/{len(expected_selectors)}")
                for selector in selectors_found:
                    print(f"     - {selector}")
                
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
                print("   ✓ Generated code preview (hover operations):")
                for i, line in enumerate(lines):
                    if 'hover_element' in line or any(sel in line for sel in expected_selectors):
                        print(f"     {i+1:2d}: {line}")
                
                os.unlink(temp_file)
                
                print("   Testing code mode execution simulation...")
                
                if "mode='code'" in generated_code:
                    print("   ✓ Generated code uses code mode configuration")
                else:
                    print("   ❌ Generated code missing code mode configuration")
                    return False
                
                if len(selectors_found) >= 3:  # At least 3 out of 4 selectors should be found
                    print("   ✓ Dual-mode selector conversion working correctly")
                    return True
                else:
                    print("   ❌ Not enough selectors converted properly")
                    return False
            else:
                print("   ❌ Not enough successful interactions for code generation")
                return False
                
        finally:
            await playwright_manager.close()
        
    except Exception as e:
        print(f"❌ Hover tool code generation verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying hover tool code generation...")
    
    result = await verify_hover_code_generation()
    
    if result:
        print("\n✅ Hover tool code generation verification passed!")
        return 0
    else:
        print("\n❌ Hover tool code generation verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
