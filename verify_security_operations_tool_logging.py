#!/usr/bin/env python3
"""
Verify that the security operations tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_security_operations_tool_logging():
    """Verify security operations tool logging integration."""
    print("=== Verifying Security Operations Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import run_security_scan, scan_for_xss, scan_for_sqli
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            print("   Testing invalid scan type...")
            result1 = await run_security_scan(
                target_url="https://example.com",
                scan_type="invalid_scan_type",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Invalid scan type result (expected failure): {result1.get('success', False)}")
            
            print("   Testing missing target URL...")
            result2 = await run_security_scan(
                target_url="",
                scan_type="xss",
                is_open_api_spec=False,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Missing URL result (expected failure): {result2.get('success', False)}")
            
            print("   Testing missing OpenAPI spec path...")
            result3 = await run_security_scan(
                target_url="https://example.com",
                scan_type="cve",
                is_open_api_spec=True,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Missing spec path result (expected failure): {result3.get('success', False)}")
            
            print("   Testing successful XSS scan...")
            result4 = await scan_for_xss(
                target_url="https://example.com",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ XSS scan result: {result4.get('success', False)}")
            if result4.get('success'):
                vulnerabilities = result4.get('vulnerabilities_found', 0)
                duration = result4.get('duration', 0)
                print(f"   ✓ Vulnerabilities found: {vulnerabilities}, Duration: {duration:.3f}s")
            
            print("   Testing successful SQL injection scan...")
            result5 = await scan_for_sqli(
                target_url="https://example.com/api",
                bearer_token="test-token-123",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ SQLi scan result: {result5.get('success', False)}")
            if result5.get('success'):
                vulnerabilities = result5.get('vulnerabilities_found', 0)
                print(f"   ✓ SQLi vulnerabilities found: {vulnerabilities}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 5:
                print("   ✓ All security operations scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        scan_type = interaction.additional_data.get('scan_type', 'N/A')
                        vulnerabilities = interaction.additional_data.get('vulnerabilities_found', 'N/A')
                        duration = interaction.additional_data.get('duration', 0)
                        error_type = interaction.additional_data.get('error_type', 'None')
                        print(f"        Scan: {scan_type}, Vulns: {vulnerabilities}, Duration: {duration:.3f}s, Error: {error_type}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "run_security_scan" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes run_security_scan and proper imports")
                        
                        expected_security_content = ["scan_type", "target_url", "xss", "sqli"]
                        security_content_found = sum(1 for content in expected_security_content if content in generated_code)
                        
                        print(f"   ✓ Security content found in code: {security_content_found}/{len(expected_security_content)}")
                        
                        if security_content_found >= 3:
                            return True
                        else:
                            print("   ❌ Generated code missing expected security content")
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
        print(f"❌ Security operations tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying security operations tool logging integration...")
    
    result = await verify_security_operations_tool_logging()
    
    if result:
        print("\n✅ Security operations tool logging verification passed!")
        return 0
    else:
        print("\n❌ Security operations tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
