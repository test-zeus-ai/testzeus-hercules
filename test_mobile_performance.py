#!/usr/bin/env python3
"""
Mobile-specific performance testing script for testzeus-hercules
"""

import asyncio
import time
import os
import sys
import json
from pathlib import Path

async def test_mobile_performance():
    """Test mobile-specific performance optimizations"""
    
    os.environ["PLANNER_MAX_CHAT_ROUND"] = "25"
    os.environ["NAV_MAX_CHAT_ROUND"] = "3"
    os.environ["SAVE_CHAT_LOGS_TO_FILE"] = "false"
    os.environ["PARALLEL_EXECUTION"] = "false"
    os.environ["RUN_DEVICE"] = "iPhone 12"
    
    print("=== Mobile Performance Test ===")
    start_time = time.time()
    
    try:
        from testzeus_hercules.core.runner import SingleCommandInputRunner
        
        mobile_test_command = "Open mobile app and navigate to settings screen"
        runner = SingleCommandInputRunner(
            stake_id="mobile_perf_test",
            command=mobile_test_command,
            planner_max_chat_round=25,
            nav_max_chat_round=3,
            dont_terminate_browser_after_run=True,
        )
        
        await runner.start()
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        print(f"Mobile test execution time: {execution_time:.2f} seconds")
        print(f"Runner execution time: {runner.execution_time:.2f} seconds")
        
        return execution_time
        
    except Exception as e:
        print(f"Mobile performance test error: {e}")
        return 0

async def test_parallel_mobile_execution():
    """Test parallel execution with mobile scenarios"""
    
    os.environ["PARALLEL_EXECUTION"] = "true"
    os.environ["MAX_PARALLEL_WORKERS"] = "2"
    
    print("\n=== Parallel Mobile Execution Test ===")
    start_time = time.time()
    
    try:
        sys.path.append("/home/ubuntu/repos/testzeus-hercules")
        from testzeus_hercules.__main__ import parallel_process
        
        mock_feature_files = [
            {
                "output_file": "/tmp/test1.feature",
                "feature": "Mobile Test 1",
                "scenario": "Login Test"
            },
            {
                "output_file": "/tmp/test2.feature", 
                "feature": "Mobile Test 2",
                "scenario": "Navigation Test"
            }
        ]
        
        with open("/tmp/test1.feature", "w") as f:
            f.write("Feature: Mobile Test 1\nScenario: Login Test\nGiven I open the app\nWhen I login\nThen I see dashboard")
        
        with open("/tmp/test2.feature", "w") as f:
            f.write("Feature: Mobile Test 2\nScenario: Navigation Test\nGiven I open the app\nWhen I navigate\nThen I see content")
        
        results = await parallel_process(mock_feature_files, max_workers=2)
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        print(f"Parallel mobile execution time: {execution_time:.2f} seconds")
        print(f"Number of results: {len(results)}")
        
        return execution_time
        
    except Exception as e:
        print(f"Parallel mobile test error: {e}")
        return 0

async def main():
    """Run mobile performance tests"""
    print("🚀 MOBILE PERFORMANCE TESTING FOR TESTZEUS-HERCULES")
    print("=" * 60)
    
    sequential_time = await test_mobile_performance()
    parallel_time = await test_parallel_mobile_execution()
    
    print("\n" + "=" * 60)
    print("MOBILE PERFORMANCE RESULTS")
    print("=" * 60)
    
    if sequential_time > 0:
        print(f"Sequential mobile test: {sequential_time:.2f} seconds")
    else:
        print("Sequential mobile test: FAILED")
    
    if parallel_time > 0:
        print(f"Parallel mobile test: {parallel_time:.2f} seconds")
        if sequential_time > 0:
            speedup = sequential_time / parallel_time if parallel_time > 0 else 0
            print(f"Parallel speedup: {speedup:.1f}x")
    else:
        print("Parallel mobile test: FAILED")
    
    print("\n📊 Mobile-specific optimizations:")
    print("- Reduced LLM chat rounds for faster mobile interactions")
    print("- Disabled chat logs for better I/O performance")
    print("- Optional parallel execution for multiple mobile tests")
    print("- Optimized Appium thread pool for mobile automation")

if __name__ == "__main__":
    asyncio.run(main())
