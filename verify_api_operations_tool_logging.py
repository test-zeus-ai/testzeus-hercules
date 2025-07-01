#!/usr/bin/env python3
"""
Verify that the API operations tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_api_operations_tool_logging():
    """Verify API operations tool logging integration."""
    print("=== Verifying API Operations Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import http_request
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            print("   Testing successful GET request...")
            result1 = await http_request(
                method="GET",
                url="https://httpbin.org/get",
                query_params={"test": "value", "logging": "verification"},
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ GET request result: {result1.get('success', False)}")
            if result1.get('success'):
                status_code = result1.get('status_code', 0)
                duration = result1.get('duration', 0)
                print(f"   ✓ Status: {status_code}, Duration: {duration:.3f}s")
            
            print("   Testing successful POST request with JSON...")
            result2 = await http_request(
                method="POST",
                url="https://httpbin.org/post",
                body={"name": "test", "action": "logging_verification"},
                body_mode="json",
                headers={"User-Agent": "testzeus-hercules-tools"},
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ POST request result: {result2.get('success', False)}")
            if result2.get('success'):
                status_code = result2.get('status_code', 0)
                print(f"   ✓ POST Status: {status_code}")
            
            print("   Testing request with Bearer authentication...")
            result3 = await http_request(
                method="GET",
                url="https://httpbin.org/bearer",
                auth_type="bearer",
                auth_value="test-token-123",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Auth request result: {result3.get('success', False)}")
            if result3.get('success'):
                status_code = result3.get('status_code', 0)
                print(f"   ✓ Auth Status: {status_code}")
            
            print("   Testing client error (404)...")
            result4 = await http_request(
                method="GET",
                url="https://httpbin.org/status/404",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ 404 request result (expected failure): {result4.get('success', False)}")
            if not result4.get('success'):
                status_code = result4.get('status_code', 0)
                print(f"   ✓ 404 Status: {status_code}")
            
            print("   Testing server error (500)...")
            result5 = await http_request(
                method="GET",
                url="https://httpbin.org/status/500",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ 500 request result (expected failure): {result5.get('success', False)}")
            if not result5.get('success'):
                status_code = result5.get('status_code', 0)
                print(f"   ✓ 500 Status: {status_code}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 5:
                print("   ✓ All API operations scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        method = interaction.additional_data.get('method', 'N/A')
                        status_code = interaction.additional_data.get('status_code', 'N/A')
                        duration = interaction.additional_data.get('duration', 0)
                        auth_type = interaction.additional_data.get('auth_type', 'None')
                        print(f"        Method: {method}, Status: {status_code}, Duration: {duration:.3f}s, Auth: {auth_type}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "http_request" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes http_request and proper imports")
                        
                        expected_http_content = ["GET", "POST", "https://", "method"]
                        http_content_found = sum(1 for content in expected_http_content if content in generated_code)
                        
                        print(f"   ✓ HTTP content found in code: {http_content_found}/{len(expected_http_content)}")
                        
                        if http_content_found >= 3:
                            return True
                        else:
                            print("   ❌ Generated code missing expected HTTP content")
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
        print(f"❌ API operations tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying API operations tool logging integration...")
    
    result = await verify_api_operations_tool_logging()
    
    if result:
        print("\n✅ API operations tool logging verification passed!")
        return 0
    else:
        print("\n❌ API operations tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
