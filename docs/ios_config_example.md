# iOS Testing Configuration Guide

This guide provides instructions for configuring TestZeus-Hercules for iOS testing.

## Environment Setup

1. Install and Configure Required Dependencies:
```bash
# Install Xcode Command Line Tools
xcode-select --install

# Accept Xcode license and set path
sudo xcodebuild -license accept
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

# Install Carthage (for WebDriverAgent)
brew install carthage

# Install ios-deploy
npm install -g ios-deploy
npm install @appium/doctor -g

# Install Appium and XCUITest driver
npm install -g appium
appium driver install xcuitest
```

2. Verify Environment Setup:
```bash
# Check Xcode configuration
xcode-select -p  # Should show Xcode path
xcrun --sdk iphonesimulator --show-sdk-version  # Should show SDK version

# Check simulator setup
xcrun simctl list runtimes  # List available iOS versions
xcrun simctl list devices   # List available simulators

# Verify Appium setup
appium-doctor --ios  # Should show all checks passing

# Test WebDriverAgent build
cd ~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/
xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination 'platform=iOS Simulator,name=iPhone 14' build-for-testing
```

If any of these checks fail, see the Troubleshooting section below.

## Configuration Example

Create a `.env` file with the following iOS-specific settings:

```bash
# Device Manager Configuration
DEVICE_MANAGER=appium
DEVICE_OS=ios

# iOS Simulator Configuration
IOS_SIMULATOR_DEVICE=iPhone 14    # Simulator device type
IOS_PLATFORM_VERSION=16.4        # iOS version
IOS_BUNDLE_ID=com.example.app    # App bundle identifier

# Appium Configuration
APPIUM_SERVER_PORT=4723
PLATFORM_NAME=iOS
AUTOMATION_NAME=XCUITest
APPIUM_IOS_APP_PATH=/path/to/your/app.app

# iOS Development Team Configuration
XCODE_ORG_ID=XXXXXXXXXX          # Your team's org ID
XCODE_SIGNING_ID=iPhone Developer
WEBDRIVERAGENT_PATH=/path/to/custom/webdriveragent  # Optional

# Appium Capabilities (as JSON string)
APPIUM_CAPABILITIES={
  "platformName": "iOS",
  "automationName": "XCUITest",
  "deviceName": "iPhone 14",
  "platformVersion": "16.4",
  "bundleId": "com.example.app",
  "xcodeOrgId": "XXXXXXXXXX",
  "xcodeSigningId": "iPhone Developer",
  "showXcodeLog": true,
  "useNewWDA": true,
  "wdaLaunchTimeout": 120000,
  "wdaConnectionTimeout": 120000,
  "shouldUseSingletonTestManager": false,
  "maxTypingFrequency": 10,
  "nativeInstrumentsLib": true
}
```

## Device Management

The system will automatically:
1. Check for running simulators
2. Start a new simulator if none are running
3. Wait for the simulator to fully boot
4. Install and launch your app
5. Begin the test session

## Supported Gestures & Actions

- Tap, swipe, and scroll gestures
- Text input with keyboard interactions
- Hardware button simulation (home, volume, etc.)
- Biometric authentication (if supported by simulator)
- App switching and system gestures

## WebDriverAgent Configuration

By default, Appium manages WebDriverAgent installation and configuration. For custom configurations:

1. Specify your WebDriverAgent path:
```bash
WEBDRIVERAGENT_PATH=/path/to/custom/webdriveragent
```

2. Configure signing:
```bash
XCODE_ORG_ID=your_team_id
XCODE_SIGNING_ID=iPhone Developer
```

## Troubleshooting

### 1. iOS SDK Issues
If you see "SDK iphonesimulator cannot be located" or similar SDK errors:
```bash
# Reset Xcode command line tools
sudo xcode-select --reset
xcode-select --install

# Verify Xcode path
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

# Update SDK path
sudo xcode-select --switch /Applications/Xcode.app

# Clean Xcode derived data
rm -rf ~/Library/Developer/Xcode/DerivedData
```

### 2. WebDriverAgent Issues
If WebDriverAgent fails to build or launch:
```bash
# Navigate to WebDriverAgent directory
cd ~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/

# Clean WebDriverAgent
rm -rf Build
xcodebuild clean

# Rebuild WebDriverAgent with detailed logging
xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination 'platform=iOS Simulator,name=iPhone 14' build-for-testing -allowProvisioningUpdates -verbose

# Check WebDriverAgent logs
tail -f /usr/local/lib/node_modules/appium/node_modules/appium-xcuitest-driver/WebDriverAgent/derived_data/Logs/Test/*.log
```

### 3. Simulator Issues
For simulator boot or detection problems:
```bash
# List all devices
xcrun simctl list devices

# Delete problematic simulator
xcrun simctl delete <device_id>

# Create fresh simulator
xcrun simctl create "iPhone 14" "com.apple.CoreSimulator.SimDeviceType.iPhone-14"

# Reset all simulators (last resort)
xcrun simctl erase all

# Kill Simulator app
killall "Simulator"
open -a Simulator
```

### 4. Appium Server Issues
For Appium connection problems:
```bash
# Run full iOS environment check
appium-doctor --ios

# Check Appium logs
tail -f ~/.appium/logs/appium.log

# Reset Appium
npm uninstall -g appium
npm cache clean --force
npm install -g appium@latest
appium driver install xcuitest
```

### 5. Runtime Issues
If you encounter errors during test execution:
```bash
# Check system logs
xcrun simctl spawn booted log stream --predicate 'processID == 0'

# Monitor WebDriverAgent
tail -f /var/log/system.log | grep -i "webdriveragent"

# Reset app permissions
xcrun simctl privacy booted reset all <bundleId>
```