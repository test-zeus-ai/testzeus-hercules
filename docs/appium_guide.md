# 🚀 Appium Setup Guide for macOS, Ubuntu, and Windows

## Overview

Appium is an open-source mobile application automation tool used for testing native, hybrid, and web applications on iOS and Android platforms.

This guide provides instructions for installing Appium and its dependencies on macOS, Ubuntu, and Windows. Additionally, a setup script is provided for automation.

## 🏗 Prerequisites

Before running Appium, ensure you have the following installed:
1. Node.js (v14+)
2. Appium CLI
3. Android SDK & ADB (for Android testing)
4. Xcode (macOS only, for iOS testing)
5. JDK (Java Development Kit)
6. Appium Inspector (GUI for inspecting elements)

## 🖥 Installation Steps

### 1️⃣ macOS Setup

#### Install Homebrew & Dependencies
```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install node
brew install watchman
brew install carthage
brew install android-platform-tools
```

#### Install Appium & Drivers
```sh
npm install -g appium
appium driver install uiautomator2
appium driver install xcuitest  # for iOS
```

#### Install Xcode (for iOS)
```sh
xcode-select --install
sudo gem install xcpretty
```

#### Download and Set Up Android SDK
1. Download the [Android SDK](https://developer.android.com/studio#downloads) (Command line tools only).
2. Extract the downloaded zip file and move it to a desired location, e.g., `~/Library/Android/sdk`.
3. Set the Android SDK path:
```sh
echo 'export ANDROID_HOME=$HOME/Library/Android/sdk' >> ~/.zshrc
echo 'export PATH=$ANDROID_HOME/emulator:$ANDROID_HOME/tools:$ANDROID_HOME/tools/bin:$ANDROID_HOME/platform-tools:$PATH' >> ~/.zshrc
source ~/.zshrc
```

#### Verify Installation
```sh
appium -v
adb devices
```

### 2️⃣ Ubuntu Setup

#### Install Dependencies
```sh
sudo apt update && sudo apt install -y nodejs npm openjdk-11-jdk android-sdk adb curl unzip
```

#### Install Appium
```sh
npm install -g appium
appium driver install uiautomator2
```

#### Download and Set Up Android SDK
1. Download the [Android SDK](https://developer.android.com/studio#downloads) (Command line tools only).
2. Extract the downloaded zip file and move it to a desired location, e.g., `~/Android/Sdk`.
3. Set the Android SDK path:
```sh
echo 'export ANDROID_HOME=$HOME/Android/Sdk' >> ~/.bashrc
echo 'export PATH=$ANDROID_HOME/emulator:$ANDROID_HOME/tools:$ANDROID_HOME/tools/bin:$ANDROID_HOME/platform-tools:$PATH' >> ~/.bashrc
source ~/.bashrc
```

#### Verify Installation
```sh
appium -v
adb devices
```

### 3️⃣ Windows Setup

#### Install Dependencies
- Download and install:
  - [Node.js](https://nodejs.org/)
  - [Java JDK](https://www.oracle.com/java/technologies/javase-jdk11-downloads.html)
  - [Android SDK](https://developer.android.com/studio#downloads) (Command line tools only)

#### Install Appium
```sh
npm install -g appium
appium driver install uiautomator2
```

#### Set Environment Variables
- Add the following to Environment Variables > System Variables:
  - `ANDROID_HOME = C:\Users\YourUser\AppData\Local\Android\Sdk`
  - `JAVA_HOME = C:\Program Files\Java\jdk-11`
  - `PATH += %ANDROID_HOME%\platform-tools; %ANDROID_HOME%\emulator`

#### Verify Installation
```sh
appium -v
adb devices
```

## ✅ Automated Setup Script

Save this script as `setup-appium.sh` and run it in macOS/Linux. For Windows, follow the manual installation steps.

```sh
#!/bin/bash

echo "🚀 Starting Appium Setup..."

# Detect OS
OS=$(uname -s)

# Install Node.js and Appium
echo "🔧 Installing Node.js and Appium..."
if [[ "$OS" == "Darwin" ]]; then
    # macOS
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    brew install node watchman carthage android-platform-tools
    npm install -g appium
    appium driver install uiautomator2
    appium driver install xcuitest
    xcode-select --install
    sudo gem install xcpretty
    echo 'export ANDROID_HOME=$HOME/Library/Android/sdk' >> ~/.zshrc
    echo 'export PATH=$ANDROID_HOME/emulator:$ANDROID_HOME/tools:$ANDROID_HOME/tools/bin:$ANDROID_HOME/platform-tools:$PATH' >> ~/.zshrc
    source ~/.zshrc
elif [[ "$OS" == "Linux" ]]; then
    # Ubuntu
    sudo apt update && sudo apt install -y nodejs npm openjdk-11-jdk android-sdk adb curl unzip
    npm install -g appium
    appium driver install uiautomator2
    echo 'export ANDROID_HOME=$HOME/Android/Sdk' >> ~/.bashrc
    echo 'export PATH=$ANDROID_HOME/emulator:$ANDROID_HOME/tools:$ANDROID_HOME/tools/bin:$ANDROID_HOME/platform-tools:$PATH' >> ~/.bashrc
    source ~/.bashrc
else
    echo "⚠️ Windows users should install manually using the instructions above."
    exit 1
fi

# Verify Installation
echo "✅ Verifying Appium installation..."
appium -v
adb devices

echo "🎉 Appium Setup Complete!"
```

## 🛠 Testing Installation

After installation, start Appium by running:
```sh
appium
```

To check if Appium is running correctly, open a new terminal and execute:
```sh
curl http://localhost:4723/status
```
It should return JSON output confirming the Appium server is running.

## 🎯 Next Steps
- Install Appium Inspector for GUI-based element inspection: [Download](https://github.com/appium/appium-inspector/releases)
- Set up Emulators/Real Devices:
  - **Android**: Use `avdmanager` to create an emulator.
  - **iOS**: Use Xcode’s simulator.

This guide and script should help you set up Appium on macOS, Ubuntu, and Windows. 🚀 Let me know if you need modifications!