#!/usr/bin/env python3
"""
Test the fixed shared logging system.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def test_fixed_logging():
    """Test that the fixed logging system works correctly."""
    print("=== Testing Fixed Shared Logging System ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig
        from testzeus_hercules_tools.tools import wait_for_seconds
        from testzeus_hercules_tools.tools.time_operations import TimeOperationsTool
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True)
        logger = InteractionLogger(config)
        logger.clear_interactions()
        
        print("1. Testing shared logging across tool instances...")
        
        tool1 = TimeOperationsTool(config)
        initial_count = len(tool1.logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        result1 = await wait_for_seconds(0.1, reason='Test 1', config=config)
        print(f"   wait_for_seconds result: {result1.get('success', False)}")
        
        count_after_1 = len(tool1.logger.get_interactions())
        print(f"   Interactions after wait_for_seconds (tool1): {count_after_1}")
        
        tool2 = TimeOperationsTool(config)
        count_tool2 = len(tool2.logger.get_interactions())
        print(f"   Interactions from tool2 instance: {count_tool2}")
        
        result2 = await wait_for_seconds(0.05, reason='Test 2', config=config)
        print(f"   Second wait_for_seconds result: {result2.get('success', False)}")
        
        final_count_tool1 = len(tool1.logger.get_interactions())
        final_count_tool2 = len(tool2.logger.get_interactions())
        print(f"   Final interactions (tool1): {final_count_tool1}")
        print(f"   Final interactions (tool2): {final_count_tool2}")
        
        fresh_logger = InteractionLogger(config)
        fresh_count = len(fresh_logger.get_interactions())
        print(f"   Fresh logger interactions: {fresh_count}")
        
        if fresh_count >= 2:
            print("✅ Shared logging system is working!")
            
            from testzeus_hercules_tools.tools import CodeGenerator
            code_generator = CodeGenerator(config)
            
            generated_code = code_generator.generate_from_logger(fresh_logger)
            print(f"   Generated code length: {len(generated_code) if generated_code else 0}")
            
            if generated_code and len(generated_code) > 100:
                print("✅ Code generation is working!")
                
                lines = generated_code.split('\n')[:15]
                print("   Generated code preview:")
                for i, line in enumerate(lines, 1):
                    print(f"     {i:2d}: {line}")
                
                return True
            else:
                print("❌ Code generation failed or produced short output")
                return False
        else:
            print("❌ Shared logging system is not working properly")
            return False
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the test."""
    print("Testing fixed shared logging system...")
    
    result = await test_fixed_logging()
    
    if result:
        print("\n✅ Fixed logging system test passed!")
        return 0
    else:
        print("\n❌ Fixed logging system test failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
