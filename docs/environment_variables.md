# Environment Variables and Configuration Guide

This document provides a comprehensive guide to all environment variables and configuration options available in TestZeus Hercules.

## Table of Contents
- [Core Environment Variables](#core-environment-variables)
- [LLM Configuration](#llm-configuration)
- [Browser Configuration](#browser-configuration)
- [Testing Configuration](#testing-configuration)
- [Device Configuration](#device-configuration)
- [Logging and Debugging](#logging-and-debugging)
- [Advanced Configuration](#advanced-configuration)
- [Configuration Priority](#configuration-priority)

## Core Environment Variables

### Mode and Project Configuration
- `MODE`: Sets the execution mode
  - Values: `prod` or `debug`
  - Default: `prod`
  - Usage: Debug mode sets timestamp to "0" and enables additional logging
  - Implementation: Used in `BaseConfigManager` to control logging verbosity and timestamp behavior

- `PROJECT_SOURCE_ROOT`: Base directory for project files
  - Default: `./opt`
  - Usage: All other paths are relative to this directory
  - Implementation: Used as the base path for all file operations and directory structures

### Directory Structure
The following paths are automatically created relative to `PROJECT_SOURCE_ROOT`:
```python
{
    "INPUT_GHERKIN_FILE_PATH": "{PROJECT_SOURCE_ROOT}/input/test.feature",
    "JUNIT_XML_BASE_PATH": "{PROJECT_SOURCE_ROOT}/output",
    "TEST_DATA_PATH": "{PROJECT_SOURCE_ROOT}/test_data",
    "SCREEN_SHOT_PATH": "{PROJECT_SOURCE_ROOT}/proofs",
    "PROJECT_TEMP_PATH": "{PROJECT_SOURCE_ROOT}/temp",
    "SOURCE_LOG_FOLDER_PATH": "{PROJECT_SOURCE_ROOT}/log_files",
    "TMP_GHERKIN_PATH": "{PROJECT_SOURCE_ROOT}/gherkin_files"
}
```

Each directory is created automatically when accessed through the corresponding getter methods in `BaseConfigManager`.

## LLM Configuration

### Basic LLM Setup
There are two ways to configure LLM, with the following priority:

1. Direct Environment Variables:
```bash
LLM_MODEL_NAME=<model_name>
LLM_MODEL_API_KEY=<api_key>
LLM_MODEL_BASE_URL=<base_url>
LLM_MODEL_API_TYPE=<api_type>
LLM_MODEL_API_VERSION=<api_version>
LLM_MODEL_PROJECT_ID=<project_id>
LLM_MODEL_REGION=<region>
```

2. Configuration File:
```bash
AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
AGENTS_LLM_CONFIG_FILE_REF_KEY=<provider_key>
```

### LLM Configuration File Structure
The `agents_llm_config.json` supports multiple providers and agent configurations. Each provider can have different configurations for different agents:

```json
{
    "<provider>": {
        "planner_agent": {
            "model_name": "<model_name>",
            "model_api_type": "<api_type>",
            "model_base_url": "<base_url>",
            "llm_config_params": {
                "temperature": 0.0,
                "top_p": 0.001,
                "seed": 12345
            }
        },
        "nav_agent": { ... },
        "mem_agent": { ... },
        "helper_agent": { ... }
    }
}
```

### Agent Types and Their Roles
- `planner_agent`: Responsible for high-level test planning and strategy
- `nav_agent`: Handles browser navigation and interaction
- `mem_agent`: Manages memory and context during test execution
- `helper_agent`: Provides support functions and utilities

### Supported LLM Parameters
Model Configuration:
- `model_name`: Name of the model to use
- `model_api_type`: Type of API (e.g., "openai", "anthropic", "azure", "mistral", "groq", "ollama")
- `model_base_url`: Base URL for API requests
- `model_api_version`: API version (if applicable)
- `model_client_host`: Client host for local models
- `model_native_tool_calls`: Enable native tool calls
- `model_hide_tools`: Tool hiding behavior

LLM Parameters:
- `temperature`: Controls randomness in responses (0.0 to 1.0)
- `top_p`: Nucleus sampling parameter (0.0 to 1.0)
- `seed`: Random seed for reproducibility

## Browser Configuration

### Browser Settings
- `BROWSER_TYPE`: Type of browser to use
  - Values: `chromium`, `firefox`, `webkit`
  - Default: `chromium`
  - Implementation: Used in `PlaywrightManager` for browser initialization

- `BROWSER_CHANNEL`: Browser channel/version
  - Values: `chrome`, `chrome-beta`, `chrome-dev`, `chrome-canary`, `msedge`, `msedge-beta`, `msedge-dev`, `msedge-canary`, `firefox`, `firefox-beta`, `firefox-dev-edition`, `firefox-nightly`
  - Default: stable channel
  - Implementation: Used to select specific browser channels for testing

- `BROWSER_VERSION`: Specific browser version
  - Values: version number or `latest`
  - Example: `114`, `115.0.1`, `latest`
  - Implementation: Controls browser version selection in `PlaywrightManager`

### Browser Behavior
- `HEADLESS`: Run browser in headless mode
  - Values: `true`, `false`
  - Default: `true`
  - Implementation: Controls browser visibility during test execution

- `BROWSER_RESOLUTION`: Browser window resolution
  - Format: `width,height`
  - Example: `1920,1080`
  - Implementation: Sets viewport size in `PlaywrightManager`

- `DONT_CLOSE_BROWSER`: Keep browser open after test
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Controls browser cleanup behavior

- `BROWSER_COOKIES`: Set cookies for the browser context
  - Format: JSON array of cookie objects
  - Example: `[{"name": "session", "value": "123456", "domain": "example.com", "path": "/"}]`
  - Default: `None` (no cookies)
  - Implementation: Cookies are added to the browser context after creation using `browserContext.add_cookies()`

## Testing Configuration

### Test Execution
- `EXECUTE_BULK`: Execute tests in bulk from tests directory
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Used in test runners for batch processing

- `REUSE_VECTOR_DB`: Reuse existing vector DB
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Controls vector database caching behavior

### Test Evidence
- `RECORD_VIDEO`: Record test execution videos
  - Values: `true`, `false`
  - Default: `true`
  - Implementation: Managed by `PlaywrightManager` for video capture

- `TAKE_SCREENSHOTS`: Take screenshots during test
  - Values: `true`, `false`
  - Default: `true`
  - Implementation: Controls screenshot capture in `PlaywrightManager`

- `CAPTURE_NETWORK`: Capture network traffic
  - Values: `true`, `false`
  - Default: `true`
  - Implementation: Enables network request/response logging

### Test Behavior
- `REACTION_DELAY_TIME`: Delay between actions
  - Default: `0.1` (seconds)
  - Implementation: Controls timing between test steps

- `USE_DYNAMIC_LTM`: Use dynamic long-term memory
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Controls memory management strategy

## Device Configuration

### Device Manager

### Device Settings
- `RUN_DEVICE`: Target device for testing
  - Example: `desktop`, `iPhone 15 Pro Max`
  - Default: `desktop`
  - Implementation: Used in `PlaywrightManager` for device emulation
  - Note: Setting to iPhone automatically switches to WebKit browser

### Geolocation
- `GEOLOCATION`: Geographic location for testing
  - Format: JSON object with latitude and longitude
  - Example: `{"latitude": 51.5, "longitude": -0.13}`

- `TIMEZONE`: Timezone for testing
  - Format: IANA timezone string
  - Example: `America/New_York`

- `LOCALE`: Locale settings
  - Default: `en-US`
  - Implementation: Controls browser language and formatting

## Logging and Debugging

### Logging Configuration
- `ENABLE_BROWSER_LOGS`: Enable browser console logging
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Captures browser console output

- `TOKEN_VERBOSE`: Enable verbose token logging
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Controls token usage logging

- `LOG_MESSAGES_FORMAT`: Format for log messages
  - Values: `text`, `json`
  - Default: `text`
  - Implementation: Controls logger output format in `logger.py`

### Debugging Tools
- `ENABLE_PLAYWRIGHT_TRACING`: Enable Playwright tracing
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Enables detailed Playwright traces

- `ENABLE_BOUNDING_BOX_SCREENSHOTS`: Enable bounding box in screenshots
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Adds visual element highlighting

- `CDP_ENDPOINT_URL`: Chrome DevTools Protocol endpoint
  - Usage: Remote debugging or custom browser instances
  - Implementation: Used for remote browser control

## Advanced Configuration

### Cache and Performance
- `HF_HOME`: Hugging Face models cache directory
  - Default: `./.cache`
  - Implementation: Controls model caching location

- `TOKENIZERS_PARALLELISM`: Enable parallel tokenization
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Controls tokenizer performance

### Telemetry and Monitoring
- `ENABLE_TELEMETRY`: Enable usage telemetry
  - Values: `0`, `1`
  - Default: `1`
  - Implementation: Controls telemetry data collection

- `AUTO_MODE`: Indicates automatic execution
  - Values: `0`, `1`
  - Default: `0`
  - Implementation: Used for telemetry and execution mode

### Additional Features
- `LOAD_EXTRA_TOOLS`: Load additional testing tools
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Controls loading of optional tools

- `COLOR_SCHEME`: Color scheme for browser
  - Values: `light`, `dark`
  - Default: `light`
  - Implementation: Sets browser color scheme preference

## Configuration Priority

The system follows this configuration priority order:

1. Command Line Arguments
2. Environment Variables
3. Configuration Files
4. Default Values

This means that command-line arguments override environment variables, which override configuration files, which override default values.
