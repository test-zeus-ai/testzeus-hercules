#!/usr/bin/env python3
"""
Comprehensive mobile performance test for testzeus-hercules
Tests mobile-specific performance optimizations and compares with web performance
"""

import asyncio
import time
import os
import sys
import json
from pathlib import Path

async def test_mobile_performance():
    """Test mobile performance with optimized settings"""
    
    os.environ["PLANNER_MAX_CHAT_ROUND"] = "25"
    os.environ["NAV_MAX_CHAT_ROUND"] = "3"
    os.environ["SAVE_CHAT_LOGS_TO_FILE"] = "false"
    os.environ["RUN_DEVICE"] = "iPhone 12"
    
    print("📱 Testing mobile performance...")
    start_time = time.time()
    
    try:
        from testzeus_hercules.core.runner import SingleCommandInputRunner
        
        mobile_command = "Open mobile app and navigate to main screen"
        
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
        return execution_time
        
    except Exception as e:
        print(f"❌ Mobile test failed: {e}")
        return 0

async def test_web_performance():
    """Test web performance for comparison"""
    
    os.environ["PLANNER_MAX_CHAT_ROUND"] = "50"
    os.environ["NAV_MAX_CHAT_ROUND"] = "5"
    os.environ["SAVE_CHAT_LOGS_TO_FILE"] = "false"
    os.environ["RUN_DEVICE"] = "desktop"
    
    print("🌐 Testing web performance...")
    start_time = time.time()
    
    try:
        from testzeus_hercules.core.runner import SingleCommandInputRunner
        
        web_command = "Navigate to https://example.com and verify page loads"
        
        runner = SingleCommandInputRunner(
            stake_id="web_perf_test",
            command=web_command,
            planner_max_chat_round=50,
            nav_max_chat_round=5,
            dont_terminate_browser_after_run=True,
        )
        
        await runner.start()
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        print(f"✓ Web test completed in {execution_time:.2f} seconds")
        return execution_time
        
    except Exception as e:
        print(f"❌ Web test failed: {e}")
        return 0

async def test_appium_operations():
    """Test Appium-specific operations"""
    
    print("🔧 Testing Appium operations...")
    
    try:
        from testzeus_hercules.core.appium_manager import AppiumManager
        
        manager = AppiumManager.get_instance()
        
        operations = [
            ("take_screenshot", lambda: manager.take_screenshot("test")),
            ("get_viewport_size", lambda: manager.get_viewport_size()),
        ]
        
        total_time = 0
        successful_ops = 0
        
        for op_name, op_func in operations:
            start_time = time.time()
            try:
                result = await op_func()
                end_time = time.time()
                op_time = end_time - start_time
                total_time += op_time
                successful_ops += 1
                print(f"  ✓ {op_name}: {op_time:.2f}s")
            except Exception as e:
                print(f"  ⚠ {op_name}: failed ({e})")
        
        avg_time = total_time / successful_ops if successful_ops > 0 else 0
        print(f"✓ Appium operations average: {avg_time:.2f}s")
        
        return avg_time
        
    except Exception as e:
        print(f"❌ Appium test failed: {e}")
        return 0

async def main():
    """Run comprehensive mobile performance test"""
    
    print("🚀 COMPREHENSIVE MOBILE PERFORMANCE TEST")
    print("=" * 60)
    
    mobile_time = await test_mobile_performance()
    web_time = await test_web_performance()
    appium_time = await test_appium_operations()
    
    print("\n" + "=" * 60)
    print("PERFORMANCE COMPARISON RESULTS")
    print("=" * 60)
    
    results = {
        "mobile_execution_time": mobile_time,
        "web_execution_time": web_time,
        "appium_operations_time": appium_time,
        "timestamp": time.time()
    }
    
    print(f"Mobile test execution:     {mobile_time:.2f}s")
    print(f"Web test execution:        {web_time:.2f}s")
    print(f"Appium operations avg:     {appium_time:.2f}s")
    
    if mobile_time > 0 and web_time > 0:
        slowdown_factor = mobile_time / web_time
        print(f"Mobile vs Web slowdown:    {slowdown_factor:.1f}x")
        
        if slowdown_factor > 2.0:
            print("🔴 SIGNIFICANT mobile performance issue")
        elif slowdown_factor > 1.5:
            print("🟡 MODERATE mobile performance issue")
        else:
            print("🟢 Mobile performance is acceptable")
        
        results["slowdown_factor"] = slowdown_factor
    
    print("\n📊 Mobile Performance Optimizations Applied:")
    print("  • Reduced Appium thread pool from 4-8 to 2-3 workers")
    print("  • Added direct execution for mobile operations")
    print("  • Reduced LLM chat rounds for mobile scenarios")
    print("  • Optimized mobile-specific file I/O operations")
    
    with open("comprehensive_mobile_performance_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n📄 Results saved to comprehensive_mobile_performance_results.json")
    
    return mobile_time > 0 and appium_time >= 0

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
