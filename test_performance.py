import asyncio
import time
from testzeus_hercules.core.appium_manager import AppiumManager

async def test_screenshot_performance():
    """Test screenshot performance improvements"""
    try:
        manager = AppiumManager.get_instance()
        print("Testing screenshot performance...")
        
        start_time = time.time()
        for i in range(3):
            result = await manager.take_screenshot(f"perf_test_{i}")
            if result:
                print(f"Screenshot {i+1} completed")
            else:
                print(f"Screenshot {i+1} skipped (no session)")
        
        end_time = time.time()
        print(f"3 screenshots took {end_time - start_time:.2f} seconds")
        
    except Exception as e:
        print(f"Performance test error: {e}")

if __name__ == "__main__":
    asyncio.run(test_screenshot_performance())
