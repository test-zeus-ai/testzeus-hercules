#!/usr/bin/env python3
"""
Verify that the input tool's interactions are properly converted to code.
"""

import asyncio
import sys
import os
import tempfile

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_input_code_generation():
    """Verify input tool code generation."""
    print("=== Verifying Input Tool Code Generation ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import enter_text, CodeGenerator
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
                    <input type="text" id="username" md="username-md-001" placeholder="Username"/>
                    <input type="email" id="email" md="email-md-002" placeholder="Email"/>
                    <textarea id="message" md="message-md-003" placeholder="Your message"></textarea>
                    <input type="password" id="password" md="password-md-004" placeholder="Password"/>
                </body>
                </html>
            """)
            
            print("   Performing various text input operations for code generation...")
            
            await enter_text(
                selector="[md='username-md-001']",
                text="john_doe",
                clear_first=True,
                config=config,
                playwright_manager=playwright_manager
            )
            
            await enter_text(
                selector="[md='email-md-002']",
                text="john.doe@example.com",
                clear_first=True,
                config=config,
                playwright_manager=playwright_manager
            )
            
            await enter_text(
                selector="[md='message-md-003']",
                text="This is a test message with multiple words and special characters: @#$%",
                clear_first=False,
                config=config,
                playwright_manager=playwright_manager
            )
            
            await enter_text(
                selector="[md='password-md-004']",
                text="SecurePassword123!",
                clear_first=True,
                config=config,
                playwright_manager=playwright_manager
            )
            
            interactions = logger.get_successful_interactions()
            print(f"   ✓ Successful interactions logged: {len(interactions)}")
            
            if len(interactions) >= 4:
                for i, interaction in enumerate(interactions, 1):
                    text_preview = interaction.additional_data.get('text', '')[:15] if interaction.additional_data else ''
                    clear_first = interaction.additional_data.get('clear_first', True) if interaction.additional_data else True
                    print(f"     {i}: {interaction.tool_name} - Text: '{text_preview}...' - Clear: {clear_first}")
                
                generated_code = code_generator.generate_from_logger(logger)
                print(f"   ✓ Generated code length: {len(generated_code)} characters")
                
                if "from testzeus_hercules_tools" in generated_code:
                    print("   ✓ Generated code imports testzeus_hercules_tools")
                else:
                    print("   ❌ Generated code missing testzeus_hercules_tools import")
                    return False
                
                if "enter_text" in generated_code:
                    print("   ✓ Generated code includes enter_text function")
                else:
                    print("   ❌ Generated code missing enter_text function")
                    return False
                
                expected_texts = ["john_doe", "john.doe@example.com", "test message", "SecurePassword123!"]
                texts_found = []
                for text in expected_texts:
                    if text in generated_code:
                        texts_found.append(text)
                
                print(f"   ✓ Input texts found in generated code: {len(texts_found)}/{len(expected_texts)}")
                
                clear_first_variations = []
                if "clear_first=True" in generated_code:
                    clear_first_variations.append("True")
                if "clear_first=False" in generated_code:
                    clear_first_variations.append("False")
                
                print(f"   ✓ Clear first variations in generated code: {clear_first_variations}")
                
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
                print("   ✓ Generated code preview (input operations):")
                for i, line in enumerate(lines):
                    if 'enter_text' in line or any(text in line for text in ['john_doe', 'example.com', 'test message']):
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
        print(f"❌ Input tool code generation verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying input tool code generation...")
    
    result = await verify_input_code_generation()
    
    if result:
        print("\n✅ Input tool code generation verification passed!")
        return 0
    else:
        print("\n❌ Input tool code generation verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
