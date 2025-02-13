# Implementation Plan: New Appium Configuration Parameters

## Overview
Add support for the following new Appium configuration parameters:
- APK path for Android
- UUID for device identification
- iOS app path
- Platform version specification

## Changes Required

### 1. Configuration Updates (config.py)
Add new configuration parameters:
```python
# Appium defaults section:
self._config.setdefault("APPIUM_APK_PATH", "")       # Path to Android APK
self._config.setdefault("APPIUM_IOS_APP_PATH", "")   # Path to iOS app
self._config.setdefault("APPIUM_DEVICE_UUID", "")    # Specific device UUID
self._config.setdefault("APPIUM_PLATFORM_VERSION", "") # OS version to target
```

Add corresponding getters:
```python
def get_appium_apk_path(self) -> str:
    """Get the path to the Android APK file."""
    return self._config["APPIUM_APK_PATH"]

def get_appium_ios_app_path(self) -> str:
    """Get the path to the iOS app file."""
    return self._config["APPIUM_IOS_APP_PATH"]

def get_appium_device_uuid(self) -> str:
    """Get the specific device UUID to target."""
    return self._config["APPIUM_DEVICE_UUID"]

def get_appium_platform_version(self) -> str:
    """Get the platform version to target."""
    return self._config["APPIUM_PLATFORM_VERSION"]
```

### 2. Appium Manager Updates (appium_manager.py)
Update initialization and session creation:

```python
def __init__(
    self,
    stake_id: Optional[str] = None,
    appium_server_url: Optional[str] = None,
    start_server: bool = True,
    server_port: int = 4723,
    platformName: Optional[str] = None,
    deviceName: Optional[str] = None,
    automationName: Optional[str] = None,
    app: Optional[str] = None,
    platformVersion: Optional[str] = None,
    udid: Optional[str] = None,
    extra_capabilities: Optional[Dict[str, Any]] = None,
):
    # ... existing init code ...
    
    self.platformVersion = platformVersion or conf.get_appium_platform_version()
    self.udid = udid or conf.get_appium_device_uuid()
    
    # Handle app paths based on platform
    if self.platformName.lower() == "android":
        self.app = app or conf.get_appium_apk_path() or conf.get_app_path()
    else:  # iOS
        self.app = app or conf.get_appium_ios_app_path() or conf.get_app_path()
```

Update session creation to use new capabilities:
```python
async def create_session(self) -> None:
    desired_caps = {
        "platformName": self.platformName,
        "deviceName": self.deviceName,
        "automationName": self.automationName,
    }
    
    if self.app:
        desired_caps["app"] = self.app
    if self.platformVersion:
        desired_caps["platformVersion"] = self.platformVersion
    if self.udid:
        desired_caps["udid"] = self.udid

    # Merge extra capabilities
    desired_caps.update(self.extra_capabilities)
```

### 3. Device Manager Updates (device_manager.py)
Update device instance creation to support new parameters:

```python
def get_device_instance(self) -> Union[AppiumManager, PlaywrightManager]:
    # ... existing code ...
    
    if device_manager == "appium":
        device_os = conf.get_device_os()
        logger.info(f"Initializing Appium manager for {device_os} device")
        
        if device_os == "ios":
            self._device_instance = AppiumManager(
                stake_id=self.stake_id,
                platformName="iOS",
                automationName="XCUITest",
                platformVersion=conf.get_appium_platform_version(),
                udid=conf.get_appium_device_uuid(),
                app=conf.get_appium_ios_app_path()
            )
        else:  # android
            self._device_instance = AppiumManager(
                stake_id=self.stake_id,
                platformName="Android",
                automationName="UiAutomator2",
                platformVersion=conf.get_appium_platform_version(),
                udid=conf.get_appium_device_uuid(),
                app=conf.get_appium_apk_path()
            )

    # ... rest of code ...
```

## Usage Example
Here's how to use the new configuration parameters:

```python
# Using environment variables
os.environ["APPIUM_APK_PATH"] = "/path/to/app.apk"
os.environ["APPIUM_DEVICE_UUID"] = "emulator-5554"
os.environ["APPIUM_PLATFORM_VERSION"] = "12"

# Or using config dictionary
config = {
    "APPIUM_APK_PATH": "/path/to/app.apk",
    "APPIUM_DEVICE_UUID": "emulator-5554",
    "APPIUM_PLATFORM_VERSION": "12",
    "DEVICE_MANAGER": "appium",
    "DEVICE_OS": "android"
}
set_global_conf(config)

# The device manager will automatically use these settings
device_manager = DeviceManager()
device = device_manager.get_device_instance()
```

## Implementation Steps

1. Add the new configuration parameters and getters to config.py
2. Update appium_manager.py constructor and create_session method
3. Update device_manager.py to pass the new parameters
4. Test with both Android and iOS configurations
5. Update documentation with new parameter usage
6. Create test cases to verify new configuration handling

## Testing Strategy

1. Test configuration loading:
   - From environment variables
   - From config dictionary
   - Default values

2. Test device creation:
   - Android with APK
   - iOS with app
   - Specific UUID
   - Platform version

3. Verify capabilities:
   - Check final desired capabilities object
   - Verify correct app path selection
   - Validate UUID passing