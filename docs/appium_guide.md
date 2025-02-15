# Appium Setup and Device Configuration Guide

[Previous content remains the same until WebDriverAgent Setup section]

## WebDriverAgent Setup for iOS

1. Install dependencies:
```sh
npm install -g appium-webdriveragent
```

2. Configure Environment Variables:
```sh
export XCODE_ORG_ID="<your-team-id>"  # Your Apple Developer Team ID
export XCODE_SIGNING_ID="iPhone Developer"
export IOS_TEAM_ID="<your-team-id>"
```

3. Build WebDriverAgent:
```sh
cd /usr/local/lib/node_modules/appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent
xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner -destination 'platform=iOS Simulator,name=iPhone 14' build-for-testing
```

4. Troubleshooting WebDriverAgent:

If you encounter build issues:

```sh
# Check Xcode path
xcode-select -p

# Run first-time Xcode setup
sudo xcodebuild -runFirstLaunch

# Accept Xcode license
sudo xcodebuild -license accept

# Set correct Xcode path
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

# Download iOS platform
xcodebuild -downloadPlatform iOS

# Verify iOS SDK installation
xcrun xcodebuild -showsdks
xcrun simctl list runtimes
```

Error Messages and Solutions:

- **"Xcode path not set"**: Run `xcode-select --install`
- **"iOS SDK not found"**: Install Xcode from App Store and run `xcodebuild -downloadPlatform iOS`
- **"WebDriverAgent not found"**: Reinstall Appium and WebDriverAgent
- **Signing issues**: Update team ID and signing certificate in Xcode project settings

For detailed iOS environment setup:

1. Install Xcode from App Store
2. Launch Xcode and complete first-time setup
3. Install command line tools:
```sh
xcode-select --install
sudo xcodebuild -license accept
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
```
4. Set up iOS Simulator:
```sh
sudo xcodebuild -runFirstLaunch
xcodebuild -downloadPlatform iOS
```
5. Verify setup:
```sh
xcrun simctl list runtimes    # Should show iOS runtimes
xcrun xcodebuild -showsdks    # Should show iOS simulator SDKs
xcrun simctl list devices     # Should show available simulators
```

[Rest of the content remains the same]