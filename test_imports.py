#!/usr/bin/env python3
"""Test script to verify all imports work correctly after merge"""

def test_device_manager():
    try:
        from testzeus_hercules.core.device_manager import DeviceManager
        print("✓ DeviceManager import successful")
        return True
    except Exception as e:
        print(f"✗ DeviceManager import failed: {e}")
        return False

def test_appium_manager():
    try:
        from testzeus_hercules.core.appium_manager import AppiumManager
        print("✓ AppiumManager import successful")
        return True
    except Exception as e:
        print(f"✗ AppiumManager import failed: {e}")
        return False

def test_mobile_nav_agent():
    try:
        from testzeus_hercules.core.agents.mobile_nav_agent import MobileNavAgent
        print("✓ MobileNavAgent import successful")
        return True
    except Exception as e:
        print(f"✗ MobileNavAgent import failed: {e}")
        return False

def test_tool_registry():
    try:
        from testzeus_hercules.core.generic_tools.tool_registry import get_all_tools
        tools = get_all_tools()
        print(f"✓ Tool registry working - found {len(tools)} tools")
        return True
    except Exception as e:
        print(f"✗ Tool registry failed: {e}")
        return False

def test_mobile_tools():
    try:
        from testzeus_hercules.core.mobile_tools.read_screen import read_screen
        from testzeus_hercules.core.mobile_tools.tap import tap
        from testzeus_hercules.core.mobile_tools.visual_skill import compare_visual_screenshot
        print("✓ Mobile tools import successful")
        return True
    except Exception as e:
        print(f"✗ Mobile tools import failed: {e}")
        return False

if __name__ == "__main__":
    print("Testing imports after merge...")
    
    tests = [
        test_device_manager,
        test_appium_manager,
        test_mobile_nav_agent,
        test_tool_registry,
        test_mobile_tools
    ]
    
    passed = 0
    for test in tests:
        if test():
            passed += 1
    
    print(f"\nResults: {passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("🎉 All imports working correctly!")
    else:
        print("⚠️  Some imports failed - check errors above")
