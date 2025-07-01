#!/usr/bin/env python3
"""
Verify that the time operations tool properly logs interactions.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_time_operations_tool_logging():
    """Verify time operations tool logging integration."""
    print("=== Verifying Time Operations Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import wait_for_seconds, wait_until_condition
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        try:
            await playwright_manager.initialize()
            
            print("   Testing successful wait_for_seconds (short wait)...")
            result1 = await wait_for_seconds(
                seconds=0.1,
                reason="Testing short wait",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Short wait result: {result1.get('success', False)}")
            if result1.get('success'):
                actual_duration = result1.get('actual_duration', 0)
                print(f"   ✓ Actual duration: {actual_duration:.3f} seconds")
            
            print("   Testing wait_for_seconds with reason...")
            result2 = await wait_for_seconds(
                seconds=0.05,
                reason="Page load stabilization",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Wait with reason result: {result2.get('success', False)}")
            
            print("   Testing failed wait_for_seconds (negative time)...")
            result3 = await wait_for_seconds(
                seconds=-1.0,
                reason="Invalid negative wait",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Negative wait result (expected failure): {result3.get('success', False)}")
            
            page = await playwright_manager.get_page()
            await page.set_content("""
                <html>
                <body>
                    <div id="status">Loading...</div>
                    <script>
                        setTimeout(() => {
                            document.getElementById('status').textContent = 'Ready';
                            window.pageReady = true;
                        }, 200);
                    </script>
                </body>
                </html>
            """)
            
            print("   Testing successful wait_until_condition...")
            result4 = await wait_until_condition(
                condition_check="window.pageReady === true",
                max_wait_seconds=2.0,
                check_interval=0.1,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Conditional wait result: {result4.get('success', False)}")
            if result4.get('success'):
                elapsed = result4.get('elapsed_seconds', 0)
                checks = result4.get('checks_performed', 0)
                print(f"   ✓ Condition met after {elapsed:.2f}s with {checks} checks")
            
            print("   Testing failed wait_until_condition (timeout)...")
            result5 = await wait_until_condition(
                condition_check="window.neverTrue === true",
                max_wait_seconds=0.3,
                check_interval=0.1,
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Timeout wait result (expected failure): {result5.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 5:
                print("   ✓ All time operations scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        if 'actual_duration' in interaction.additional_data:
                            duration = interaction.additional_data.get('actual_duration', 0)
                            reason = interaction.additional_data.get('reason', 'N/A')
                            print(f"        Duration: {duration:.3f}s, Reason: {reason}")
                        if 'elapsed_seconds' in interaction.additional_data:
                            elapsed = interaction.additional_data.get('elapsed_seconds', 0)
                            checks = interaction.additional_data.get('checks_performed', 0)
                            condition_met = interaction.additional_data.get('condition_met', False)
                            print(f"        Elapsed: {elapsed:.2f}s, Checks: {checks}, Met: {condition_met}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if ("wait_for_seconds" in generated_code or "wait_until_condition" in generated_code) and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes time operation functions and proper imports")
                        
                        expected_functions = ["wait_for_seconds", "wait_until_condition"]
                        functions_found = sum(1 for func in expected_functions if func in generated_code)
                        
                        print(f"   ✓ Time operation functions found in code: {functions_found}/{len(expected_functions)}")
                        
                        if functions_found >= 1:
                            return True
                        else:
                            print("   ❌ Generated code missing expected time operation functions")
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
        print(f"❌ Time operations tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying time operations tool logging integration...")
    
    result = await verify_time_operations_tool_logging()
    
    if result:
        print("\n✅ Time operations tool logging verification passed!")
        return 0
    else:
        print("\n❌ Time operations tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
