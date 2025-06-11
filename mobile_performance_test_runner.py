#!/usr/bin/env python3
"""
Mobile performance test runner for testzeus-hercules
Specifically tests mobile scenarios to measure performance improvements
"""

import asyncio
import time
import os
import sys
import json
from pathlib import Path

async def run_mobile_test_scenario():
    """Run a mobile test scenario and measure execution time"""
    
    os.environ["PLANNER_MAX_CHAT_ROUND"] = "25"
    os.environ["NAV_MAX_CHAT_ROUND"] = "3"
    os.environ["SAVE_CHAT_LOGS_TO_FILE"] = "false"
    os.environ["RUN_DEVICE"] = "iPhone 12"
    
    print("📱 Running mobile test scenario...")
    start_time = time.time()
    
    try:
        from testzeus_hercules.core.runner import SingleCommandInputRunner
        
        mobile_command = "Open mobile app and navigate to settings screen"
        
        runner = SingleCommandInputRunner(
            stake_id="mobile_perf_test",
            command=mobile_command,
            planner_max_chat_round=25,
            nav_max_chat_round=3,
            dont_terminate_browser_after_run=True,
        )
        
        await runner.start()
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        print(f"✓ Mobile test completed in {execution_time:.2f} seconds")
        return execution_time, runner.execution_time
        
    except Exception as e:
        print(f"❌ Mobile test failed: {e}")
        return 0, 0

async def test_mobile_appium_operations():
    """Test Appium-specific operations for mobile"""
    
    print("🔧 Testing mobile Appium operations...")
    
    try:
        from testzeus_hercules.core.appium_manager import AppiumManager
        
        manager = AppiumManager.get_instance()
        
        operations_time = 0
        operations_count = 0
        
        mobile_operations = [
            ("take_screenshot", lambda: manager.take_screenshot("mobile_test")),
            ("get_viewport_size", lambda: manager.get_viewport_size()),
        ]
        
        for op_name, op_func in mobile_operations:
            start_time = time.time()
            try:
                result = await op_func()
                end_time = time.time()
                op_time = end_time - start_time
                operations_time += op_time
                operations_count += 1
                print(f"  ✓ {op_name}: {op_time:.2f}s")
            except Exception as e:
                print(f"  ⚠ {op_name}: skipped ({e})")
        
        avg_time = operations_time / operations_count if operations_count > 0 else 0
        print(f"✓ Mobile Appium operations average: {avg_time:.2f}s")
        
        return avg_time
        
    except Exception as e:
        print(f"❌ Mobile Appium test failed: {e}")
        return 0

async def test_mobile_tools_performance():
    """Test mobile tools performance"""
    
    print("🛠 Testing mobile tools performance...")
    
    try:
        mobile_tools = [
            "testzeus_hercules.core.mobile_tools.read_screen",
            "testzeus_hercules.core.mobile_tools.tap",
            "testzeus_hercules.core.mobile_tools.visual_skill",
        ]
        
        tools_loaded = 0
        load_time = 0
        
        for tool_path in mobile_tools:
            start_time = time.time()
            try:
                module_parts = tool_path.split('.')
                module_name = '.'.join(module_parts[:-1])
                class_name = module_parts[-1]
                
                module = __import__(module_name, fromlist=[class_name])
                getattr(module, class_name)
                
                end_time = time.time()
                tool_time = end_time - start_time
                load_time += tool_time
                tools_loaded += 1
                
                print(f"  ✓ {class_name}: {tool_time:.3f}s")
                
            except Exception as e:
                print(f"  ⚠ {class_name}: failed ({e})")
        
        avg_load_time = load_time / tools_loaded if tools_loaded > 0 else 0
        print(f"✓ Mobile tools average load time: {avg_load_time:.3f}s")
        
        return avg_load_time, tools_loaded
        
    except Exception as e:
        print(f"❌ Mobile tools test failed: {e}")
        return 0, 0

async def main():
    """Main mobile performance test function"""
    
    print("🚀 MOBILE PERFORMANCE TEST RUNNER")
    print("=" * 50)
    
    test_time, runner_time = await run_mobile_test_scenario()
    appium_time = await test_mobile_appium_operations()
    tools_time, tools_count = await test_mobile_tools_performance()
    
    print("\n" + "=" * 50)
    print("MOBILE PERFORMANCE RESULTS")
    print("=" * 50)
    
    results = {
        "mobile_test_time": test_time,
        "runner_execution_time": runner_time,
        "appium_operations_time": appium_time,
        "mobile_tools_load_time": tools_time,
        "mobile_tools_loaded": tools_count,
        "timestamp": time.time()
    }
    
    print(f"Mobile test execution:     {test_time:.2f}s")
    print(f"Runner execution time:     {runner_time:.2f}s")
    print(f"Appium operations avg:     {appium_time:.2f}s")
    print(f"Mobile tools load avg:     {tools_time:.3f}s")
    print(f"Mobile tools loaded:       {tools_count}")
    
    performance_score = 0
    if test_time > 0 and test_time < 60:
        performance_score += 25
    if appium_time > 0 and appium_time < 5:
        performance_score += 25
    if tools_time > 0 and tools_time < 1:
        performance_score += 25
    if tools_count >= 2:
        performance_score += 25
    
    print(f"\n📊 Mobile Performance Score: {performance_score}/100")
    
    if performance_score >= 75:
        print("🟢 EXCELLENT mobile performance")
    elif performance_score >= 50:
        print("🟡 GOOD mobile performance")
    else:
        print("🔴 POOR mobile performance - needs optimization")
    
    with open("mobile_performance_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📄 Results saved to mobile_performance_results.json")
    
    return performance_score >= 50

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
