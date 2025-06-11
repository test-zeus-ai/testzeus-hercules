# TestZeus Hercules Project Structure Guide

## Single Test Mode (Non-Bulk)
When `EXECUTE_BULK=false` (default), Hercules uses `opt` as the project base directory:

```
opt/
├── input/                    # Place your feature files here
│   └── test.feature
├── gherkin_files/           # Generated/processed feature files
│   └── <scenario_name>.feature
├── output/                  # Test results 
│   └── run_<timestamp>/
│       ├── test.feature_result.html
│       └── test.feature_result.xml
├── log_files/              # Execution logs
│   └── <scenario_name>/
│       └── run_<timestamp>/
│           ├── agent_inner_thoughts.json
│           └── log_between_sender*.json
├── proofs/                 # Execution artifacts
│   └── <scenario_name>/
│       └── run_<timestamp>/
│           ├── screenshots/
│           ├── videos/
│           └── network_logs.json
└── test_data/             # Test data files
    └── test_data.txt

```

## Bulk Test Mode
When `EXECUTE_BULK=true`, each subdirectory under `tests/` acts as an independent project base:

```
opt/
└── tests/
    ├── test1/            # Independent test project
    │   ├── input/
    │   ├── output/
    │   ├── log_files/
    │   ├── proofs/
    │   └── test_data/
    └── test2/            # Another test project
        ├── input/
        │   └── test2.feature
        ├── output/
        │   └── run_<timestamp>/
        ├── log_files/
        │   └── <scenario_name>/
        ├── proofs/
        │   └── <scenario_name>/
        └── test_data/
```

## Key Points
1. In non-bulk mode:
   - Project base is `./opt`
   - Single test execution
   - Results stored directly under `opt/`

2. In bulk mode:
   - Each folder under `tests/` is treated as a separate project
   - Tests run sequentially for each project
   - Results stored in respective project folders

## Using Bulk Mode

1. Enable bulk execution:
```bash
export EXECUTE_BULK=true
```

2. Structure tests in separate directories:
```bash
mkdir -p opt/tests/test1 opt/tests/test2
```

3. Each test directory should contain:
- `input/` with feature files
- `test_data/` with test data
- Other directories created automatically

Hercules will process each test directory sequentially, maintaining separate logs and artifacts for each test suite.

## Running Hercules

### Using Python Package

1. Install the package:
```bash
pip install testzeus-hercules
playwright install --with-deps
```

2. Run a test with basic options:
```bash
testzeus-hercules --project-base=opt
```

Or with individual parameters:
```bash
testzeus-hercules --input-file opt/input/test.feature --output-path opt/output --test-data-path opt/test_data
```

3. LLM Configuration Options:

```bash
# Basic LLM configuration
testzeus-hercules --llm-model gpt-4o --llm-model-api-key your-api-key

# Advanced LLM configuration
testzeus-hercules --llm-model claude-3-opus-20240229 \
                  --llm-model-api-key your-api-key \
                  --llm-model-api-type anthropic \
                  --llm-model-base-url https://api.anthropic.com \
                  --llm-temperature 0.0

# LLM configuration file
testzeus-hercules --agents-llm-config-file ./agents_llm_config.json \
                  --agents-llm-config-file-ref-key openai

# Portkey integration
testzeus-hercules --enable-portkey \
                  --portkey-api-key your-portkey-api-key \
                  --portkey-strategy fallback
```

4. Browser Options:

```bash
# Browser selection
testzeus-hercules --browser-channel chrome-beta \
                  --browser-version 115.0.1 \
                  --browser-path /path/to/chrome

# Browser extensions
testzeus-hercules --enable-ublock
```

5. Other Options:

```bash
# Bulk execution
testzeus-hercules --bulk

# Vector DB reuse
testzeus-hercules --reuse-vector-db

# Screen sharing
testzeus-hercules --auto-accept-screen-sharing
```

## Detailed Run Instructions

### Using Python Package

#### Non-Bulk Mode (Default)
1. Prepare your project structure:
```bash
mkdir -p opt/input opt/output opt/test_data
cp your-test.feature opt/input/
cp your-test-data.json opt/test_data/
```

2. Run a single test:
```bash
testzeus-hercules --project-base=opt
```

Expected outcome:
- Test runs from `opt/input/test.feature`
- Results appear in `opt/output/run_<timestamp>/`
- Single execution, single set of results

#### Bulk Mode
1. Enable bulk mode:
```bash
export EXECUTE_BULK=true
```

2. Prepare multiple test directories:
```bash
mkdir -p opt/tests/test1/input opt/tests/test1/test_data
mkdir -p opt/tests/test2/input opt/tests/test2/test_data
cp test1.feature opt/tests/test1/input/
cp test2.feature opt/tests/test2/input/
```

3. Run all tests:
```bash
testzeus-hercules --project-base=opt
```

Expected outcome:
- Tests run sequentially from each directory under `opt/tests/`
- Each test has its own results directory
- Multiple executions, separate results per test

## LLM Configuration

Hercules requires LLM configuration to function properly. There are two main approaches to configure LLMs:

### 1. Direct Environment Variables

The simplest approach is to set the required environment variables:

```bash
# Basic LLM configuration (required)
export LLM_MODEL_NAME=gpt-4o
export LLM_MODEL_API_KEY=your-api-key

# Optional LLM configuration
export LLM_MODEL_BASE_URL=https://api.openai.com/v1
export LLM_MODEL_API_TYPE=openai  # openai, anthropic, azure, mistral, groq, etc.
export LLM_MODEL_TEMPERATURE=0.0
```

All supported LLM environment variables:
```bash
# Model Configuration
export LLM_MODEL_NAME=gpt-4o                  # Name of the model
export LLM_MODEL_API_KEY=your-api-key         # API key
export LLM_MODEL_BASE_URL=https://api.openai.com/v1  # Base URL for API
export LLM_MODEL_API_TYPE=openai              # API type (openai, anthropic, etc)
export LLM_MODEL_API_VERSION=2023-05-15       # API version (if applicable)
export LLM_MODEL_PROJECT_ID=my-project        # Project ID (for GCP-based models)
export LLM_MODEL_REGION=us-central1           # Region (for region-specific models)
export LLM_MODEL_CLIENT_HOST=localhost:8000   # Client host (for local models)
export LLM_MODEL_NATIVE_TOOL_CALLS=true       # Enable native tool calls
export LLM_MODEL_HIDE_TOOLS=false             # Hide tools from the model

# AWS Bedrock-specific Configuration
export LLM_MODEL_AWS_REGION=us-east-1         # AWS region
export LLM_MODEL_AWS_ACCESS_KEY=your-key      # AWS access key
export LLM_MODEL_AWS_SECRET_KEY=your-secret   # AWS secret key
export LLM_MODEL_AWS_PROFILE_NAME=default     # AWS profile name
export LLM_MODEL_AWS_SESSION_TOKEN=token      # AWS session token

# Other Model Settings
export LLM_MODEL_PRICING=0.01                 # Model pricing for tracking

# LLM Parameter Configuration
export LLM_MODEL_TEMPERATURE=0.0              # Temperature (0.0-1.0) 
export LLM_MODEL_CACHE_SEED=12345             # Seed for response caching
export LLM_MODEL_SEED=67890                   # Random seed for reproducibility
export LLM_MODEL_MAX_TOKENS=4096              # Maximum tokens in response
export LLM_MODEL_PRESENCE_PENALTY=0.0         # Presence penalty
export LLM_MODEL_FREQUENCY_PENALTY=0.0        # Frequency penalty
export LLM_MODEL_STOP='END'                   # Stop sequence
```

### 2. Configuration File

For more advanced setups, especially when using multiple models or providers, use a JSON configuration file:

```bash
export AGENTS_LLM_CONFIG_FILE=./agents_llm_config.json
export AGENTS_LLM_CONFIG_FILE_REF_KEY=openai  # The provider to use
```

Example `agents_llm_config.json`:
```json
{
  "openai": {
    "planner_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    },
    "nav_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    },
    "mem_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    },
    "helper_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    }
  },
  "anthropic": {
    "planner_agent": {
      "model_name": "claude-3-opus-20240229",
      "model_api_key": "your-anthropic-api-key",
      "model_api_type": "anthropic",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    },
    "nav_agent": {
      "model_name": "claude-3-sonnet-20240229",
      "model_api_key": "your-anthropic-api-key",
      "model_api_type": "anthropic",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    },
    "mem_agent": {
      "model_name": "claude-3-haiku-20240307",
      "model_api_key": "your-anthropic-api-key",
      "model_api_type": "anthropic",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    },
    "helper_agent": {
      "model_name": "claude-3-haiku-20240307",
      "model_api_key": "your-anthropic-api-key",
      "model_api_type": "anthropic",
      "llm_config_params": {
        "temperature": 0.0,
        "top_p": 0.001
      }
    }
  }
}
```

### Using Portkey for LLM Routing

Hercules supports [Portkey](https://portkey.ai/) for intelligent LLM request routing, fallbacks, and monitoring:

```bash
# Enable Portkey integration
export ENABLE_PORTKEY=true
export PORTKEY_API_KEY=your-portkey-api-key

# Optional Portkey configuration
export PORTKEY_STRATEGY=fallback  # fallback or loadbalance
export PORTKEY_CACHE_ENABLED=false
export PORTKEY_RETRY_COUNT=3
export PORTKEY_TIMEOUT=30.0

# Advanced configuration (JSON format)
export PORTKEY_TARGETS='[{"provider": "openai", "weight": 0.7}, {"provider": "anthropic", "weight": 0.3}]'
export PORTKEY_GUARDRAILS='{"topics": ["harmful", "illegal"], "action": "filter"}'
```

When Portkey is enabled, all LLM requests are routed through the Portkey gateway, providing:
- Fallback capabilities between different providers
- Load balancing across providers
- Request tracing and monitoring
- Semantic caching (when enabled)
- Content filtering and guardrails

### Using Docker

1. Pull the image:
```bash
docker pull testzeus/hercules:latest
```

2. Run with environment file:
```bash
docker run --env-file=.env \
  -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json \
  -v ./opt:/testzeus-hercules/opt \
  --rm -it testzeus/hercules:latest
```

#### Non-Bulk Mode (Default)
1. Prepare your local directory structure:
```bash
mkdir -p opt/input opt/output opt/test_data
cp your-test.feature opt/input/
cp your-test-data.json opt/test_data/
```

2. Run the container:
```bash
docker run --env-file=.env \
  -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json \
  -v ./opt:/testzeus-hercules/opt \
  --rm -it testzeus/hercules:latest
```

Important Note: Docker runs are always in headless mode (no visible browser). To connect to a visible browser, you'll need to use the CDP_ENDPOINT_URL environment variable to connect to an external browser instance.

#### Bulk Mode in Docker
1. Set up environment file (`.env`):
```bash
EXECUTE_BULK=true
# ... other environment variables ...
```

2. Prepare multiple test directories locally:
```bash
mkdir -p opt/tests/test1/input opt/tests/test1/test_data
mkdir -p opt/tests/test2/input opt/tests/test2/test_data
# Copy your feature files and test data to respective directories
```

3. Run the container:
```bash
docker run --env-file=.env \
  -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json \
  -v ./opt:/testzeus-hercules/opt \
  --rm -it testzeus/hercules:latest
```

Expected outcome:
- Tests execute sequentially in headless mode
- Each test directory processed independently
- Results stored in respective test directories under `opt/tests/`

### Connecting to External Browser (Docker Mode)
To run tests with a visible browser while using Docker:

1. Set up a Chrome instance with remote debugging enabled
2. Add to your `.env` file:
```bash
CDP_ENDPOINT_URL=wss://your-chrome-host:9222
```

This allows Hercules running in Docker to connect to an external browser instance, enabling visible test execution even when running in a container.

### Browser Cookie Configuration

To set cookies for the browser context, you can use the `BROWSER_COOKIES` environment variable:

```bash
export BROWSER_COOKIES='[{"name": "session", "value": "123456", "domain": "example.com", "path": "/", "httpOnly": true, "secure": true}]'
```

The cookies must be provided as a JSON array of cookie objects with the following properties:
- `name`: Cookie name (required)
- `value`: Cookie value (required)
- `domain`: Cookie domain (required)
- `path`: Cookie path (defaults to "/")
- `httpOnly`: Whether the cookie is HTTP-only (defaults to false)
- `secure`: Whether the cookie is secure (defaults to false)
- `expires`: Cookie expiration time in seconds since epoch (defaults to -1, session cookie)

Cookies are added to the browser context after it's created, ensuring compatibility with all browser types (Chromium, Firefox, WebKit).

## Remote Browser Testing Platforms

Hercules supports running tests on various remote browser platforms using the CDP (Chrome DevTools Protocol) endpoint URL. Here's how to configure different providers:

### BrowserStack

1. Set up your credentials:
```bash
export BROWSERSTACK_USERNAME=your_username
export BROWSERSTACK_ACCESS_KEY=your_access_key
```

2. Generate CDP URL:
```bash
export CDP_ENDPOINT_URL=$(python helper_scripts/browser_stack_generate_cdp_url.py)
```

Note: Video recording is not supported with BrowserStack as it uses the connect API.

### LambdaTest

1. Set up your credentials:
```bash
export LAMBDATEST_USERNAME=your_username
export LAMBDATEST_ACCESS_KEY=your_access_key
```

2. Generate CDP URL:
```bash
export CDP_ENDPOINT_URL=$(python helper_scripts/lambda_test_generate_cdp_url.py)
```

Note: Video recording is not supported with LambdaTest as it uses the connect API.

### BrowserBase

1. Set the CDP URL directly:
```bash
export CDP_ENDPOINT_URL=wss://connect.browserbase.com?apiKey=your_api_key
```

BrowserBase supports connect_over_cdp, so video recording will work on the test execution host.

### AnchorBrowser

1. Set the CDP URL directly:
```bash
export CDP_ENDPOINT_URL=wss://connect.anchorbrowser.io?apiKey=your_api_key
```

AnchorBrowser supports connect_over_cdp, so video recording will work on the test execution host.

### Important Notes About Remote Browser Testing

- When using CDP_ENDPOINT_URL, Hercules will connect to the remote browser instance instead of launching a local browser.
- Video recording capabilities depend on the platform:
  - Platforms using Playwright's connect API (BrowserStack, LambdaTest) do not support video recording
  - Platforms using connect_over_cdp (BrowserBase, AnchorBrowser) support video recording on the test execution host
- Each platform may have different capabilities and configurations available through their respective helper scripts
- Remote browser testing is particularly useful when running tests in Docker containers or CI/CD pipelines

## Viewing Results

After test execution, results can be found in several locations:

1. **Test Reports**
   - HTML Report: `opt/output/run_<timestamp>/test.feature_result.html`
   - XML Report: `opt/output/run_<timestamp>/test.feature_result.xml`

2. **Execution Logs**
   - Agent Thoughts: `opt/log_files/<scenario_name>/run_<timestamp>/agent_inner_thoughts.json`
   - Communication Logs: `opt/log_files/<scenario_name>/run_<timestamp>/log_between_sender*.json`

3. **Execution Artifacts**
   - Screenshots: `opt/proofs/<scenario_name>/run_<timestamp>/screenshots/`
   - Videos: `opt/proofs/<scenario_name>/run_<timestamp>/videos/`
   - Network Logs: `opt/proofs/<scenario_name>/run_<timestamp>/network_logs.json`
