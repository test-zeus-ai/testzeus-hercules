#!/usr/bin/env python3
"""
Simple test to verify logging functionality works.
"""

import asyncio
import sys
import os

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def test_simple_logging():
    """Test simple logging functionality."""
    print("=== Testing Simple Logging ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig
        from testzeus_hercules_tools.tools import wait_for_seconds, InteractionLogger
        
        config = ToolsConfig(mode='agent', headless=True, enable_logging=True)
        print(f"✓ Config created: mode={config.mode}, logging={config.enable_logging}")
        
        logger = InteractionLogger(config)
        print(f"✓ Logger created: {type(logger)}")
        
        await logger.log_interaction(
            tool_name="test_tool",
            selector="test_selector",
            action="test_action",
            success=True,
            mode="agent",
            additional_data={"test": "data"}
        )
        print("✓ Manual interaction logged")
        
        interactions = logger.get_successful_interactions()
        print(f"✓ Logged interactions: {len(interactions)}")
        
        if interactions:
            print("✓ Logging is working!")
            for i, interaction in enumerate(interactions):
                print(f"  Interaction {i}: {interaction}")
            return True
        else:
            print("✗ No interactions found")
            return False
        
    except Exception as e:
        print(f"✗ Simple logging test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run simple logging test."""
    print("Starting simple logging test...")
    
    result = await test_simple_logging()
    
    if result:
        print("\n✓ Simple logging test passed!")
        return 0
    else:
        print("\n✗ Simple logging test failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
