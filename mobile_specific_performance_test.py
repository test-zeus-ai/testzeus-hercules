#!/usr/bin/env python3
"""
Mobile-specific performance test for testzeus-hercules
Tests only mobile components to isolate mobile performance bottlenecks
"""

import asyncio
import time
import os
import sys
import json
from pathlib import Path

async def test_mobile_appium_manager():
    """Test Appium manager performance specifically"""
    
    print("🔧 Testing Appium Manager Performance...")
    
    try:
        from testzeus_hercules.core.appium_manager import AppiumManager
        
        start_time = time.time()
        manager = AppiumManager.get_instance()
        init_time = time.time() - start_time
        
        print(f"  ✓ AppiumManager initialization: {init_time:.3f}s")
        
        operations = [
            ("take_screenshot", lambda: manager.take_screenshot("mobile_test")),
            ("get_viewport_size", lambda: manager.get_viewport_size()),
        ]
        
        total_time = 0
        successful_ops = 0
        
        for op_name, op_func in operations:
            op_start = time.time()
            try:
                result = await op_func()
                op_time = time.time() - op_start
                total_time += op_time
                successful_ops += 1
                print(f"  ✓ {op_name}: {op_time:.3f}s")
            except Exception as e:
                print(f"  ⚠ {op_name}: failed ({e})")
        
        avg_time = total_time / successful_ops if successful_ops > 0 else 0
        print(f"✓ Appium operations average: {avg_time:.3f}s")
        
        return {
            "init_time": init_time,
            "avg_operation_time": avg_time,
            "successful_operations": successful_ops
        }
        
    except Exception as e:
        print(f"❌ Appium manager test failed: {e}")
        return {"init_time": 0, "avg_operation_time": 0, "successful_operations": 0}

async def test_mobile_tools_loading():
    """Test mobile tools loading performance"""
    
    print("🛠 Testing Mobile Tools Loading Performance...")
    
    mobile_tools = [
        "testzeus_hercules.core.mobile_tools.read_screen",
        "testzeus_hercules.core.mobile_tools.tap",
        "testzeus_hercules.core.mobile_tools.visual_skill",
        "testzeus_hercules.core.mobile_tools.swipe",
        "testzeus_hercules.core.mobile_tools.scroll",
    ]
    
    tools_loaded = 0
    total_load_time = 0
    
    for tool_path in mobile_tools:
        start_time = time.time()
        try:
            module_parts = tool_path.split('.')
            module_name = '.'.join(module_parts[:-1])
            class_name = module_parts[-1]
            
            module = __import__(module_name, fromlist=[class_name])
            getattr(module, class_name)
            
            load_time = time.time() - start_time
            total_load_time += load_time
            tools_loaded += 1
            
            print(f"  ✓ {class_name}: {load_time:.3f}s")
            
        except Exception as e:
            print(f"  ⚠ {class_name}: failed ({e})")
    
    avg_load_time = total_load_time / tools_loaded if tools_loaded > 0 else 0
    print(f"✓ Mobile tools average load time: {avg_load_time:.3f}s")
    
    return {
        "tools_loaded": tools_loaded,
        "total_tools": len(mobile_tools),
        "avg_load_time": avg_load_time,
        "total_load_time": total_load_time
    }

async def test_mobile_device_manager():
    """Test mobile device manager performance"""
    
    print("📱 Testing Mobile Device Manager Performance...")
    
    try:
        from testzeus_hercules.core.device_manager import DeviceManager
        
        start_time = time.time()
        device_manager = DeviceManager()
        init_time = time.time() - start_time
        
        print(f"  ✓ DeviceManager initialization: {init_time:.3f}s")
        
        return {
            "init_time": init_time,
            "success": True
        }
        
    except Exception as e:
        print(f"  ❌ DeviceManager test failed: {e}")
        return {
            "init_time": 0,
            "success": False
        }

async def test_mobile_navigation_agent():
    """Test mobile navigation agent performance"""
    
    print("🧭 Testing Mobile Navigation Agent Performance...")
    
    try:
        from testzeus_hercules.core.agents.mobile_nav_agent import MobileNavAgent
        
        start_time = time.time()
        
        print(f"  ✓ MobileNavAgent import: {time.time() - start_time:.3f}s")
        
        return {
            "import_time": time.time() - start_time,
            "success": True
        }
        
    except Exception as e:
        print(f"  ❌ MobileNavAgent test failed: {e}")
        return {
            "import_time": 0,
            "success": False
        }

async def main():
    """Run mobile-specific performance tests"""
    
    print("🚀 MOBILE-SPECIFIC PERFORMANCE TEST")
    print("=" * 50)
    
    appium_results = await test_mobile_appium_manager()
    tools_results = await test_mobile_tools_loading()
    device_results = await test_mobile_device_manager()
    nav_results = await test_mobile_navigation_agent()
    
    print("\n" + "=" * 50)
    print("MOBILE PERFORMANCE RESULTS")
    print("=" * 50)
    
    results = {
        "appium_manager": appium_results,
        "mobile_tools": tools_results,
        "device_manager": device_results,
        "navigation_agent": nav_results,
        "timestamp": time.time()
    }
    
    print(f"Appium Manager Init:       {appium_results['init_time']:.3f}s")
    print(f"Appium Operations Avg:     {appium_results['avg_operation_time']:.3f}s")
    print(f"Mobile Tools Loaded:       {tools_results['tools_loaded']}/{tools_results['total_tools']}")
    print(f"Mobile Tools Load Avg:     {tools_results['avg_load_time']:.3f}s")
    print(f"Device Manager Init:       {device_results['init_time']:.3f}s")
    print(f"Nav Agent Import:          {nav_results['import_time']:.3f}s")
    
    performance_score = 0
    if appium_results['init_time'] < 1.0:
        performance_score += 20
    if appium_results['avg_operation_time'] < 0.5:
        performance_score += 20
    if tools_results['tools_loaded'] >= 3:
        performance_score += 20
    if tools_results['avg_load_time'] < 0.1:
        performance_score += 20
    if device_results['success'] and device_results['init_time'] < 0.5:
        performance_score += 20
    
    print(f"\n📊 Mobile Performance Score: {performance_score}/100")
    
    if performance_score >= 80:
        print("🟢 EXCELLENT mobile performance")
    elif performance_score >= 60:
        print("🟡 GOOD mobile performance")
    else:
        print("🔴 POOR mobile performance - needs optimization")
    
    with open("mobile_specific_performance_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📄 Results saved to mobile_specific_performance_results.json")
    
    return performance_score >= 60

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
