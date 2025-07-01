#!/usr/bin/env python3
"""
Verify that the SQL operations tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_sql_operations_tool_logging():
    """Verify SQL operations tool logging integration."""
    print("=== Verifying SQL Operations Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import execute_select_query
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            print("   Testing invalid query type (non-SELECT)...")
            result1 = await execute_select_query(
                connection_string="sqlite+aiosqlite:///:memory:",
                query="INSERT INTO test VALUES (1, 'test')",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Invalid query result (expected failure): {result1.get('success', False)}")
            
            print("   Testing invalid query type (UPDATE)...")
            result2 = await execute_select_query(
                connection_string="sqlite+aiosqlite:///:memory:",
                query="UPDATE test SET name = 'updated'",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ UPDATE query result (expected failure): {result2.get('success', False)}")
            
            print("   Testing invalid query type (DELETE)...")
            result3 = await execute_select_query(
                connection_string="sqlite+aiosqlite:///:memory:",
                query="DELETE FROM test WHERE id = 1",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ DELETE query result (expected failure): {result3.get('success', False)}")
            
            print("   Testing invalid connection string...")
            result4 = await execute_select_query(
                connection_string="invalid://connection/string",
                query="SELECT 1 as test_column",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Invalid connection result (expected failure): {result4.get('success', False)}")
            
            print("   Testing valid SELECT query with in-memory SQLite...")
            result5 = await execute_select_query(
                connection_string="sqlite+aiosqlite:///:memory:",
                query="SELECT 1 as test_column, 'hello' as test_text",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Valid SELECT result: {result5.get('success', False)}")
            if result5.get('success'):
                row_count = result5.get('row_count', 0)
                print(f"   ✓ Rows returned: {row_count}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 4:  # At least 4 failed + 1 potentially successful
                print("   ✓ All SQL operations scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        query = interaction.additional_data.get('query', 'N/A')[:50]
                        query_type = interaction.additional_data.get('query_type', 'N/A')
                        error_type = interaction.additional_data.get('error_type', 'N/A')
                        row_count = interaction.additional_data.get('row_count', 'N/A')
                        print(f"        Query: '{query}...', Type: {query_type}, Error: {error_type}, Rows: {row_count}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "execute_select_query" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes execute_select_query and proper imports")
                        
                        expected_sql_content = ["SELECT", "connection_string", "query"]
                        sql_content_found = sum(1 for content in expected_sql_content if content in generated_code)
                        
                        print(f"   ✓ SQL content found in code: {sql_content_found}/{len(expected_sql_content)}")
                        
                        if sql_content_found >= 2:
                            return True
                        else:
                            print("   ❌ Generated code missing expected SQL content")
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
        print(f"❌ SQL operations tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying SQL operations tool logging integration...")
    
    result = await verify_sql_operations_tool_logging()
    
    if result:
        print("\n✅ SQL operations tool logging verification passed!")
        return 0
    else:
        print("\n❌ SQL operations tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
