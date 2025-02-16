# Appium Environment Variables Guide

## Overview
This document describes all Appium-related environment variables supported by TestZeus Hercules.

## Server Configuration
- `APPIUM_SERVER_URL`: Custom Appium server URL (optional, defaults to localhost)
- `APPIUM_SERVER_PORT`: Port for Appium server (default: 4723)

## Device Configuration
- `DEVICE_MANAGER`: Device automation manager type ("appium" or "playwright")
- `DEVICE_OS`: Target device OS type ("android" or "ios")
- `PLATFORM_NAME`: Platform name for Appium capabilities ("Android" or "iOS")
- `DEVICE_NAME`: Device identifier (e.g., "emulator-5554" for Android, "iPhone Simulator" for iOS)
- `AUTOMATION_NAME`: Automation engine ("UiAutomator2" for Android, "XCUITest" for iOS)

## Application Configuration
- `APP_PATH`: Path to the application package/bundle
- `APPIUM_APK_PATH`: Path to Android APK file
- `APPIUM_IOS_APP_PATH`: Path to iOS app file
- `APPIUM_DEVICE_UUID`: Specific device UUID to target
- `APPIUM_PLATFORM_VERSION`: OS version to target
- `APPIUM_CAPABILITIES`: Additional Appium capabilities as JSON string

## Android Configuration
- `EMULATOR_AVD_NAME`: Android Virtual Device name (default: "Medium_Phone_API_35")
- `ANDROID_HOME`: Path to Android SDK installation
- `ANDROID_AVD_HOME`: Path to Android Virtual Device files

## iOS Configuration
- `IOS_SIMULATOR_DEVICE`: iOS simulator device type (e.g., "iPhone 14")
- `IOS_BUNDLE_ID`: iOS app bundle identifier
- `XCODE_ORG_ID`: Xcode organization/team ID
- `XCODE_SIGNING_ID`: Xcode signing identity (default: "iPhone Developer")
- `WEBDRIVERAGENT_PATH`: Custom WebDriverAgent path
- `IOS_TEAM_ID`: iOS development team ID

## Example Configuration

### Android Example
```bash
export DEVICE_MANAGER="appium"
export DEVICE_OS="android"
export PLATFORM_NAME="Android"
export DEVICE_NAME="emulator-5554"
export AUTOMATION_NAME="UiAutomator2"
export APPIUM_APK_PATH="/path/to/app.apk"
export EMULATOR_AVD_NAME="Pixel_4_API_30"
```

### iOS Example
```bash
export DEVICE_MANAGER="appium"
export DEVICE_OS="ios"
export PLATFORM_NAME="iOS"
export DEVICE_NAME="iPhone 14"
export AUTOMATION_NAME="XCUITest"
export APPIUM_IOS_APP_PATH="/path/to/app.app"
export IOS_BUNDLE_ID="com.example.app"
export XCODE_ORG_ID="YOUR_TEAM_ID"
export IOS_TEAM_ID="YOUR_TEAM_ID"
```

## Environment File
You can create a `.env` file in your project root with these variables. Example:

```bash
# Appium Server Config
APPIUM_SERVER_URL=http://localhost:4723

# Device Config
DEVICE_MANAGER=appium
DEVICE_OS=android
PLATFORM_NAME=Android
DEVICE_NAME=emulator-5554

# App Config
APP_PATH=/path/to/app.apk