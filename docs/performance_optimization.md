# Performance Optimization Guide

## Overview

This document outlines the performance optimizations implemented in testzeus-hercules to address slow test execution times.

## Key Performance Improvements

### 1. Reduced LLM Chat Rounds

**Problem**: Default configuration allowed up to 500 chat rounds per planner agent and 10 per navigation agent, resulting in excessive LLM API calls.

**Solution**: Reduced defaults to 50 planner rounds and 5 navigation rounds for web, 25 and 3 for mobile (90% and 70% reduction respectively).

**Configuration**:
```bash
# Web tests
export PLANNER_MAX_CHAT_ROUND=50
export NAV_MAX_CHAT_ROUND=5

# Mobile tests (more aggressive optimization)
export PLANNER_MAX_CHAT_ROUND=25
export NAV_MAX_CHAT_ROUND=3
```

### 2. Parallel Test Execution

**Problem**: Tests were processed sequentially, one at a time.

**Solution**: Added optional parallel processing with configurable worker count.

**Configuration**:
```bash
export PARALLEL_EXECUTION=true
export MAX_PARALLEL_WORKERS=3
```

### 3. Optimized File I/O

**Problem**: Chat logs were saved by default, causing I/O overhead.

**Solution**: Disabled chat log saving by default for better performance.

**Configuration**:
```bash
export SAVE_CHAT_LOGS_TO_FILE=false
```

### 4. Mobile-Specific Optimizations

**Problem**: Mobile tests were significantly slower than web tests due to Appium overhead and device emulation.

**Solution**: 
- Reduced Appium thread pool from 4-8 to 2-4 workers for mobile scenarios
- Extended direct execution to more mobile operations (screenshots, page source, URL)
- Optimized mobile tool execution and device manager operations

**Configuration**:
```bash
export APPIUM_THREAD_POOL_SIZE=2
export MOBILE_SCREENSHOT_DIRECT=true
```

### 5. Performance Monitoring

**Enhancement**: Added detailed timing breakdown for device operations, LLM calls, and file I/O.</str>

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PARALLEL_EXECUTION` | `false` | Enable parallel test execution |
| `MAX_PARALLEL_WORKERS` | `3` | Maximum number of parallel workers |
| `PLANNER_MAX_CHAT_ROUND` | `50` | Maximum chat rounds for planner agent |
| `NAV_MAX_CHAT_ROUND` | `5` (web) / `3` (mobile) | Maximum chat rounds for navigation agent |
| `SAVE_CHAT_LOGS_TO_FILE` | `false` | Enable chat log file saving |
| `APPIUM_THREAD_POOL_SIZE` | `2` | Appium thread pool size for mobile |
| `MOBILE_SCREENSHOT_DIRECT` | `true` | Enable direct mobile screenshot execution |
| `SAVE_CHAT_LOGS_TO_FILE` | `false` | Enable chat log file saving (disabled by default for performance) |</str_str>

## Expected Performance Improvements

- **80-90% reduction** in LLM API calls per test case (500→50 planner rounds)
- **3-5x faster execution** when using parallel mode
- **Significant reduction** in file I/O overhead (chat logs disabled by default)
- **Better visibility** into performance bottlenecks through detailed timing logs
- **Mobile-specific optimizations** for Appium thread pool and device operations

## Testing Performance

Use the included performance testing scripts:

```bash
# Basic performance test
python test_execution_performance.py

# Comprehensive comparison
python performance_comparison.py

# Mobile-specific performance test
python test_mobile_performance.py

# Mobile vs web benchmark
python mobile_vs_web_benchmark.py

# Apply mobile optimizations
python optimize_mobile_performance.py

# Test parallel execution
PARALLEL_EXECUTION=true MAX_PARALLEL_WORKERS=3 python -m testzeus_hercules

# Mobile performance test runner
python mobile_performance_test_runner.py
```

## Backward Compatibility

All optimizations are configurable via environment variables. To restore original behavior:

```bash
export PLANNER_MAX_CHAT_ROUND=500
export NAV_MAX_CHAT_ROUND=10
export SAVE_CHAT_LOGS_TO_FILE=true
export PARALLEL_EXECUTION=false
```
