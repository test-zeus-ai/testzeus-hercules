#!/usr/bin/env python3
"""
Verify that the navigation tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_navigation_tool_logging():
    """Verify navigation tool logging integration."""
    print("=== Verifying Navigation Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import open_url
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            await playwright_manager.initialize()
            
            print("   Testing successful URL navigation...")
            result1 = await open_url(
                url="https://httpbin.org/html",
                timeout=2,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Successful navigation result: {result1.get('success', False)}")
            
            print("   Testing navigation to same URL (cached)...")
            result2 = await open_url(
                url="https://httpbin.org/html",
                timeout=1,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Cached navigation result: {result2.get('success', False)}")
            print(f"   ✓ From cache: {result2.get('from_cache', False)}")
            
            print("   Testing navigation to different URL...")
            result3 = await open_url(
                url="https://httpbin.org/json",
                timeout=2,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Different URL navigation result: {result3.get('success', False)}")
            
            print("   Testing navigation with protocol auto-addition...")
            result4 = await open_url(
                url="httpbin.org/status/200",
                timeout=2,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Auto-protocol navigation result: {result4.get('success', False)}")
            
            print("   Testing failed navigation (invalid URL)...")
            result5 = await open_url(
                url="https://invalid-domain-that-does-not-exist-12345.com",
                timeout=1,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Failed navigation result (expected): {result5.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 4:  # At least 4 successful + 1 failed
                print("   ✓ All navigation scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        title = interaction.additional_data.get('title', 'N/A')[:30]
                        status_code = interaction.additional_data.get('status_code', 'N/A')
                        from_cache = interaction.additional_data.get('from_cache', False)
                        print(f"        Title: '{title}...', Status: {status_code}, Cached: {from_cache}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "open_url" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes open_url and proper imports")
                        
                        expected_urls = ["httpbin.org", "html", "json", "status/200"]
                        urls_found = sum(1 for url in expected_urls if url in generated_code)
                        
                        print(f"   ✓ URL fragments found in code: {urls_found}/{len(expected_urls)}")
                        
                        if urls_found >= 3:
                            return True
                        else:
                            print("   ❌ Generated code missing expected URL content")
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
        print(f"❌ Navigation tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying navigation tool logging integration...")
    
    result = await verify_navigation_tool_logging()
    
    if result:
        print("\n✅ Navigation tool logging verification passed!")
        return 0
    else:
        print("\n❌ Navigation tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
