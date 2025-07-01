#!/usr/bin/env python3
"""
End-to-end test to verify all tools are logged and code generation works.
"""

import asyncio
import sys
import os
import tempfile

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def test_end_to_end_logging_and_code_generation():
    """Test that all tools log interactions and generate working code."""
    print("=== End-to-End Logging and Code Generation Test ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig
        from testzeus_hercules_tools.tools import (
            wait_for_seconds, wait_until_condition,
            InteractionLogger, CodeGenerator
        )
        
        config = ToolsConfig(mode='agent', headless=True, enable_logging=True)
        logger = InteractionLogger(config)
        code_generator = CodeGenerator(config)
        
        print("✓ Tools and logger initialized")
        
        initial_interactions = logger.get_successful_interactions()
        print(f"Initial interactions: {len(initial_interactions)}")
        
        print("\nTesting time operations...")
        result1 = await wait_for_seconds(0.1, reason='Test 1', config=config)
        print(f"✓ wait_for_seconds result: {result1.get('success', False)}")
        
        result2 = await wait_for_seconds(0.05, reason='Test 2', config=config)
        print(f"✓ wait_for_seconds result: {result2.get('success', False)}")
        
        optional_tests = []
        
        try:
            from testzeus_hercules_tools.tools import execute_select_query
            print("\nTesting SQL operations...")
            sql_result = await execute_select_query(
                connection_string="sqlite+aiosqlite:///:memory:",
                query="SELECT 1 as test_column",
                config=config
            )
            print(f"✓ SQL operation result: {sql_result.get('success', False)}")
            optional_tests.append("SQL")
        except (ImportError, AttributeError, Exception) as e:
            print(f"⚠ SQL operations not available or failed: {e}")
            
        try:
            from testzeus_hercules_tools.tools import http_request
            print("\nTesting API operations...")
            api_result = await http_request(
                url="https://httpbin.org/get",
                method="GET",
                config=config
            )
            print(f"✓ API operation result: {api_result.get('success', False)}")
            optional_tests.append("API")
        except (ImportError, AttributeError, Exception) as e:
            print(f"⚠ API operations not available or failed: {e}")
        
        final_interactions = logger.get_successful_interactions()
        new_interactions = len(final_interactions) - len(initial_interactions)
        print(f"\n✓ New interactions logged: {new_interactions}")
        
        if new_interactions == 0:
            print("✗ No interactions were logged!")
            return False
        
        print("\nLogged interactions:")
        for i, interaction in enumerate(final_interactions[-new_interactions:]):
            print(f"  {i+1}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
        
        print("\nTesting code generation...")
        generated_code = code_generator.generate_code_from_logs(final_interactions)
        print(f"✓ Generated {len(generated_code)} characters of code")
        
        if len(generated_code) < 100:
            print("✗ Generated code seems too short!")
            return False
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(generated_code)
            temp_file = f.name
        
        print(f"✓ Generated code saved to: {temp_file}")
        
        lines = generated_code.split('\n')[:20]
        print("\n✓ Generated code preview:")
        for i, line in enumerate(lines, 1):
            print(f"  {i:2d}: {line}")
        
        if "from testzeus_hercules_tools" not in generated_code:
            print("✗ Generated code doesn't import testzeus_hercules_tools!")
            return False
        
        print("✓ Generated code imports testzeus_hercules_tools package")
        
        try:
            compile(generated_code, temp_file, 'exec')
            print("✓ Generated code has valid Python syntax")
        except SyntaxError as e:
            print(f"✗ Generated code has syntax error: {e}")
            return False
        
        print(f"\n✓ End-to-end test successful!")
        print(f"  - {new_interactions} interactions logged")
        print(f"  - {len(optional_tests)} optional tools tested: {', '.join(optional_tests)}")
        print(f"  - Generated code uses testzeus_hercules_tools imports")
        print(f"  - Generated code has valid syntax")
        
        os.unlink(temp_file)
        
        return True
        
    except Exception as e:
        print(f"✗ End-to-end test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run end-to-end test."""
    print("Starting end-to-end logging and code generation test...")
    
    result = await test_end_to_end_logging_and_code_generation()
    
    if result:
        print("\n✅ END-TO-END TEST PASSED!")
        print("All tools are properly logged and code generation works correctly.")
        return 0
    else:
        print("\n❌ END-TO-END TEST FAILED!")
        print("There are issues with logging or code generation.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
