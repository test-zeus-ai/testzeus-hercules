# Appium Configuration Changes Plan

## Overview
Need to implement several missing configuration methods in the `BaseConfigManager` class to support Appium functionality in `appium_manager.py`.

## Missing Methods to Implement
1. `get_appium_server_url()`
   - Returns remote Appium server URL if configured
   - Default: None (local server will be used)

2. `get_appium_server_port()`
   - Returns port number for Appium server
   - Default: 4723

3. `get_platform_name()`
   - Returns platform name for Appium capabilities
   - Default: "Android"

4. `get_device_name()`
   - Returns device name for Appium capabilities
   - Default: "emulator-5554"

5. `get_automation_name()`
   - Returns automation name for Appium capabilities
   - Default: "UiAutomator2"

6. `get_app_path()`
   - Returns path to the app package/bundle
   - Default: ""

7. `get_appium_capabilities()`
   - Returns any extra capabilities for Appium session
   - Default: {}

8. `get_emulator_avd_name()`
   - Returns AVD name for Android emulator
   - Default: "Pixel_3_API_30"

## Environment Variables to Add
Add the following to .env-example:
```
# Appium Configuration
APPIUM_SERVER_URL=
APPIUM_SERVER_PORT=4723
PLATFORM_NAME=Android
DEVICE_NAME=emulator-5554
AUTOMATION_NAME=UiAutomator2
APP_PATH=
APPIUM_CAPABILITIES={}
EMULATOR_AVD_NAME=Pixel_3_API_30
```

## Implementation Steps
1. Add these environment variables to `.env-example`
2. Add the new configuration methods to `BaseConfigManager` in `config.py`
3. Update the `_merge_from_env()` method to include these new variables
4. Update `_finalize_defaults()` to set default values for these configurations

## Next Steps
1. Switch to Code mode to implement these changes
2. Test the implementation by running Appium tests