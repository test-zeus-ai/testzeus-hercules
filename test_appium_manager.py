import asyncio
from typing import Dict
from testzeus_hercules.core.appium_manager import AppiumManager

async def main() -> None:
    # Get the single instance of AppiumManager (with stake_id, e.g., "123")
    appium_mgr = AppiumManager(stake_id="123")

    # Either create an emulator and connect (if using a local Appium server)
    # or initialize the session directly if a remote Appium URL is provided.
    await appium_mgr.create_emulator_and_connect(avd_name="Medium_Phone_API_35")

    # Start session recording.
    await appium_mgr.start_screen_recording()

    # Perform some interactions.
    await appium_mgr.click_by_accessibility("loginButton")
    await appium_mgr.enter_text_by_accessibility("usernameField", "testuser")
    await appium_mgr.clear_text_by_accessibility("passwordField")
    await appium_mgr.long_press_by_accessibility("menuItem", duration=1500)
    await appium_mgr.perform_tap(100, 200)
    await appium_mgr.perform_swipe(100, 200, 300, 400)

    # Retrieve and print the simplified accessibility tree.
    tree = await appium_mgr.get_accessibility_tree()
    print(tree)

    # Capture device logs.
    await appium_mgr.capture_device_logs()

    # Stop screen recording.
    video_path = await appium_mgr.stop_screen_recording()
    print(f"Screen recording saved at: {video_path}")

    # Get the current viewport size.
    viewport = await appium_mgr.get_viewport_size()
    print(f"Viewport size: {viewport}")

    # Get the current screen as a PIL Image and show it.
    image = await appium_mgr.see_screen()
    if image:
        image.show()  # Opens the image using the default image viewer.

    # Use the high-level cleanup method
    AppiumManager.close_instance("123")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # Ensure all instances are closed on exit
        AppiumManager.close_all_instances()
