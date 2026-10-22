# Environment Variables and Configuration Guide

This document provides a guide to the main environment variables and
configuration options available in TestZeus Hercules. It covers the variables
you will typically set; a few niche switches are documented closer to the
feature that uses them (see `docs/run_guide.md`, `docs/mcp_server.md`, and the
orchestration sections of the README).

## Table of Contents
- [Core Environment Variables](#core-environment-variables)
  - [Mode and Project Configuration](#mode-and-project-configuration)
  - [Directory Structure](#directory-structure)
- [LLM Configuration](#llm-configuration)
  - [Basic LLM Setup](#basic-llm-setup)
  - [LLM Configuration File Structure](#llm-configuration-file-structure)
  - [Agent Types and Their Roles](#agent-types-and-their-roles)
  - [Supported LLM Parameters](#supported-llm-parameters)
- [Browser Configuration](#browser-configuration)
  - [Browser Settings](#browser-settings)
  - [Browser Behavior](#browser-behavior)
- [Testing Configuration](#testing-configuration)
  - [Test Execution](#test-execution)
  - [Test Evidence](#test-evidence)
  - [Test Behavior](#test-behavior)
- [Device Configuration](#device-configuration)
  - [Device Settings](#device-settings)
  - [Geolocation](#geolocation)
- [Logging and Debugging](#logging-and-debugging)
  - [Logging Configuration](#logging-configuration)
  - [Debugging Tools](#debugging-tools)
- [Advanced Configuration](#advanced-configuration)
  - [Cache and Performance](#cache-and-performance)
  - [Portkey Integration](#portkey-integration)
  - [MCP Integration](#mcp-integration)
  - [Run History and Workspace Hygiene](#run-history-and-workspace-hygiene)
  - [Telemetry and Monitoring](#telemetry-and-monitoring)
  - [Additional Features](#additional-features)
- [Reliability, Reporting and Strictness Flags](#reliability-reporting-and-strictness-flags)
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
Hercules supports two LLM setup paths. Use direct `LLM_MODEL_*` variables for a
simple single-model run. Use `agents_llm_config.json` plus an active
provider/profile key when you want separate planner, navigation, memory, and
helper model settings.

1. Configuration File:
```bash
AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
AGENTS_LLM_CONFIG_FILE_REF_KEY=<provider_key>
```

The `<provider_key>` must match a top-level key in `agents_llm_config.json`.
LiteLLM is one supported OpenAI-compatible proxy option, but the top-level key
is user-configurable.

2. Legacy Direct Environment Variables:
```bash
LLM_MODEL_NAME=<model_name>
LLM_MODEL_API_KEY=<api_key>
LLM_MODEL_BASE_URL=<base_url>
LLM_MODEL_API_TYPE=<api_type>
LLM_MODEL_API_VERSION=<api_version>
LLM_MODEL_PROJECT_ID=<project_id>
LLM_MODEL_REGION=<region>
LLM_MODEL_CLIENT_HOST=<client_host>
LLM_MODEL_NATIVE_TOOL_CALLS=<true|false>
LLM_MODEL_HIDE_TOOLS=<true|false>
LLM_MODEL_AWS_REGION=<aws_region>
LLM_MODEL_AWS_ACCESS_KEY=<aws_access_key>
LLM_MODEL_AWS_SECRET_KEY=<aws_secret_key>
LLM_MODEL_AWS_PROFILE_NAME=<aws_profile_name>
LLM_MODEL_AWS_SESSION_TOKEN=<aws_session_token>
LLM_MODEL_PRICING=<pricing>
```

- `MODEL_API_KEY`: last-resort API key fallback on the direct-LLM path
  - Usage: when neither `api_key`/`model_api_key` in the model config nor
    `LLM_MODEL_API_KEY` provides a key, `utils/llm_helper.py` falls back to
    `MODEL_API_KEY`; if that is also unset, model creation fails with
    "LLM API key is missing. Set MODEL_API_KEY or model_api_key in config."

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
        "helper_agent": { ... }
    }
}
```

### Agent Types and Their Roles
- `planner_agent`: LangGraph planner node. It must produce strict planner JSON
  and should use a model that is strong at structured reasoning.
- `nav_agent`: Shared by browser, API, security, SQL, time keeper, MCP, and
  executor helpers. It must use a model with reliable tool calling.
- `helper_agent`: Visual/multimodal helper model.

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
- `seed`: Random seed for reproducibility
- `cache_seed`: Seed for caching. The LangGraph/LangChain path strips this
  value before constructing LangChain models, so do not rely on it for runtime
  determinism.
- `max_tokens`: Maximum number of tokens in response (for GPT-3, GPT-4, Claude, and other models)
- `max_completion_tokens`: Maximum number of tokens in response (for GPT-5 and newer models)
- `presence_penalty`: Penalty for token presence
- `frequency_penalty`: Penalty for token frequency
- `stop`: Custom stop sequences

**Note**: For GPT-5 models (gpt-5, gpt-5-mini, gpt-5-nano), use `max_completion_tokens` instead of `max_tokens`. The system will automatically convert between these parameters based on the model family.

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

- `BROWSER_STORAGE_DIR`: Persistent browser user-data directory override
  - Values: Path to a directory
  - Default: unset (an empty temporary user-data dir is used per launch)
  - Usage: When set, the directory's contents are copied into the browser's
    user-data dir, so browser profile state (storage, cookies, extensions) can
    persist across runs
  - Implementation: Read in `core/playwright_manager.py` when creating the browser context

## Testing Configuration

### Test Execution
- `SKIP_BROWSER`: Do not launch a browser for the run
  - Values: `1`, `true`, `yes` (case-insensitive); anything else keeps the browser
  - Default: unset (browser enabled)
  - Implementation: For pure API/MCP/SQL scenarios; skips the Chromium startup per scenario and the empty screenshot/video folders

- `EXECUTE_BULK`: Execute tests in bulk from tests directory
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Used in test runners for batch processing

- `EXIT_CODE_COMPAT`: Legacy always-exit-0 behavior
  - Values: `1` to enable; any other value leaves CI gating active
  - Default: unset (Hercules exits `1` when any scenario fails or errors)
  - Usage: When set to `1`, the CLI and the orchestrators (`run_manager`, `hercules-suite`, `hercules-monitor`) exit `0` even on failures
  - Implementation: Checked in `testzeus_hercules/__main__.py` and `orchestrator/run_manager.py`, `orchestrator/suites.py`, `orchestrator/scheduler.py`

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

- `GUIDED_MODE`: Enable guided test generation mode
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Used by the CLI to generate and run a feature from a natural-language test description

- `GUIDED_TEST_DESCRIPTION`: Natural-language test description for guided mode
  - Usage: Equivalent to passing `--test "..."` on the CLI

- `GUIDED_DRY_RUN`: Generate and print Gherkin without running it
  - Values: `true`, `false`
  - Usage: Equivalent to passing `--dry-run`

- `EDITOR`: Editor used for guided feature editing
  - Values: Any editor command launchable as `<editor> <file>`
  - Default: `nano`
  - Usage: Launched by the interactive guided test builder (`utils/test_builder.py`) when you pick the edit option for the generated feature

### Python Sandbox
- `SANDBOX_MODE`: Execution mode of `execute_python_sandbox`
  - Values: `inprocess`, `subprocess`
  - Default: `inprocess` (script runs in the agent process with the live `page`/`browser`/`context`/`playwright_manager`)
  - Usage: `subprocess` runs each script in a disposable child process with CPU/memory rlimits and a hard timeout (SIGKILL of the process group). Live browser objects cannot cross the process boundary there: set `CDP_ENDPOINT_URL` and the child re-attaches over CDP, otherwise `page`/`browser`/... raise a clear error. Neither mode is a security boundary.

- `SANDBOX_TENANT_ID`: Sandbox permission profile
  - Values: `executor_agent`, `data_agent`, `api_agent`, `restricted_agent`
  - Default: empty (base injections only)

- `SANDBOX_PACKAGES`: Comma-separated modules to inject into sandbox scripts
  - Example: `requests,pandas,numpy`

- `SANDBOX_CUSTOM_INJECTIONS`: JSON object describing extra modules and custom objects
  - Example: `{"modules": ["jwt"], "custom_objects": {"MAX_RETRIES": 3}}`

### API Verification Tools
- `API_REQUEST_TIMEOUT`: HTTP timeout in seconds for `generic_http_api`
  - Default: `5`

- `API_ALLOWED_PATHS`: Extra folders model-supplied file paths may live in (multipart uploads, Hurl suites, OpenAPI specs)
  - Separator: `os.pathsep` (`:` on POSIX)
  - Default: empty (only `TEST_DATA_PATH` and `PROJECT_SOURCE_ROOT` are allowed)

- `ENABLE_UI_ASSERTIONS`: Register the `assert_ui` / `capture_ui_value` tools on the browser, MCP and mobile agents
  - Values: `true`, `false`
  - Default: `false`
  - See `docs/api_verification.md`

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

- `LOG_FILE_PATH`: Optional rotating log file
  - Values: A writable file path
  - Default: unset (logs go to the console only)
  - Usage: When set, runtime logs are also written to this file as a rotating log (10 MB per file, 5 backups) — useful for post-run inspection in CI
  - Implementation: Read in `testzeus_hercules/utils/logger.py`

- `SAVE_CHAT_LOGS_TO_FILE`: Persist agent chat logs to files
  - Values: `true`, `false`
  - Default: `true`
  - Implementation: Read in `core/runner.py`; controls whether the agents' chat logs are saved to disk during a run

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

### Portkey Integration
- `ENABLE_PORTKEY`: Enable Portkey LLM gateway integration
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Enables Portkey for LLM request routing and management

- `PORTKEY_API_KEY`: API key for Portkey
  - Required when `ENABLE_PORTKEY=true`
  - Implementation: Used for authentication with Portkey services

- `PORTKEY_STRATEGY`: Request routing strategy
  - Values: `fallback`, `loadbalance`
  - Default: `fallback`
  - Implementation: Controls how requests are routed across multiple providers
  - Note: In fallback mode, requests try the first provider and fall back to others if it fails
  - Note: In loadbalance mode, requests are distributed across all configured providers

- `PORTKEY_CACHE_ENABLED`: Enable semantic caching for LLM requests
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Caches semantically similar requests to reduce API costs

- `PORTKEY_CACHE_TTL`: Time-to-live for cached responses in seconds
  - Default: `3600` (1 hour)
  - Implementation: Controls how long cached responses remain valid

- `PORTKEY_TARGETS`: JSON-formatted list of target providers
  - Format: JSON array of provider configurations
  - Implementation: Defines which providers to use for routing
  - Example: `[{"provider": "openai", "weight": 0.7}, {"provider": "anthropic", "weight": 0.3}]`

- `PORTKEY_GUARDRAILS`: JSON-formatted guardrail configurations
  - Format: JSON object with guardrail settings
  - Implementation: Defines safety and content filtering rules
  - Example: `{"topics": ["harmful", "illegal"], "action": "filter"}`

- `PORTKEY_RETRY_COUNT`: Number of retry attempts for failed requests
  - Default: `3`
  - Implementation: Controls how many times Portkey retries failed requests

- `PORTKEY_TIMEOUT`: Timeout for LLM requests in seconds
  - Default: `30.0`
  - Implementation: Controls how long Portkey waits for a response before timing out

### MCP Integration
- `MCP_ENABLED`: Enable MCP integration
  - Values: `true`, `false`
  - Default: `false`
  - Implementation: Toggles MCP agent and tooling. When enabled, Hercules can connect to configured MCP servers.

- `MCP_SERVERS`: MCP server configuration
  - Values: Path to a `.json` file or a JSON string
  - Example (file path): `mcp_servers.json`
  - Example (JSON string): `{"mcpServers": {"server_name": {"transport": "streamable-http", "url": "<session.mcp.url>"}}}`
  - Implementation: If a path ending with `.json` is provided, Hercules attempts to load it from:
    - The current working directory
    - Relative to `testzeus_hercules/`
    - Relative to `testzeus_hercules/config.py` location
    Otherwise, it treats the value as a JSON string.

- `MCP_TIMEOUT`: MCP read timeout in seconds
  - Default: `30`
  - Implementation: Used to set `ClientSession` read timeouts during MCP interactions.

Hercules can also run as an MCP server through `testzeus-hercules-mcp`.
The server entrypoint uses these variables:

- `TESTZEUS_ROOT`: Repository or project root for server-side test execution
- `TESTZEUS_PYTHON`: Reserved for helpers that shell out; currently unused by the in-process MCP server (it does not spawn subprocesses)
- `MCP_HOST`: Bind host, default `127.0.0.1` (loopback). A non-loopback value is refused unless `MCP_AUTH_TOKEN` is set
- `MCP_PORT`: Bind port, default `8000`
- `MCP_PATH`: HTTP path, default `/mcp`
- `MCP_AUTH_TOKEN`: Bearer token required on every request when set (>= 16 chars; mandatory for non-loopback binds)
- `MCP_ALLOWED_HOSTS`: Extra comma-separated `Host` header names accepted by the MCP server
- `HERCULES_MCP_ALLOWED_ROOTS`: `os.pathsep`-separated extra directories that MCP `project_path`/`prd_path` may point into (defaults: `TESTZEUS_ROOT`/cwd, `PROJECT_SOURCE_ROOT`, `TEST_DATA_PATH`)
- `HERCULES_MCP_AUTO_APPROVE_PLAN`: `true` runs PRD-derived test plans without the `approve_plan` confirmation (default `false`)
- `PRD_FETCH_ALLOW_PRIVATE_HOSTS`: `true` lets PRD URLs point at private/loopback hosts (default `false`: public http(s) only, redirects re-validated)
- `HERCULES_MCP_EXEC_TIMEOUT`: Per-run execution deadline in seconds for the MCP run tools, default `1800`; invalid values fall back to the default

### Run History and Workspace Hygiene
- `HERCULES_HISTORY_DB`: Run-history SQLite database location
  - Values: Database URL or file path
  - Default: `<PROJECT_SOURCE_ROOT>/hercules_history.db` (i.e. `opt/hercules_history.db`)
  - Usage: Overrides where the run history is stored; `hercules-dashboard` reads the same DB. Parallel/suite runners pin this for scenario subprocesses so history is not lost with the throwaway workspaces.
  - Implementation: Used in `reporting/run_db.py` and `orchestrator/run_manager.py`

- `HERCULES_KEEP_WORKSPACES`: Keep scenario workspaces for debugging
  - Values: `1` to enable; any other value leaves cleanup active
  - Default: unset (successful workspaces are cleaned up; failed workspaces are always kept)
  - Usage: Setting `1` keeps parallel/suite scenario workspaces and the compiled-plan tempdirs of plan-only suites after the run, for debugging
  - Implementation: Used in `orchestrator/run_manager.py` and `orchestrator/suites.py`

- `HERCULES_RUN_RETENTION_HOURS`: Retention window for scheduled suite runs
  - Values: Integer number of hours
  - Default: `72`
  - Usage: `hercules-monitor` sweeps `.hercules/suite_runs/` trees and `.hercules/parallel_*` run-manager workroots older than this many hours before every cycle (the run manager also sweeps old `.hercules/parallel_*` workroots before each run); trees younger than 1 hour are never swept regardless of the setting
  - Implementation: Used in `orchestrator/scheduler.py` and `orchestrator/run_manager.py`

### Telemetry and Monitoring
- `ENABLE_TELEMETRY`: Enable usage telemetry
  - Values: `0`, `1`
  - Default: `1` (telemetry enabled)
  - Set `ENABLE_TELEMETRY=0` to disable.
  - When enabled, captures: a randomly generated installation ID, the email you optionally provide at first run, a config snapshot, and folder paths (which may include your OS username).

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

- `ADDITIONAL_TOOL_DIRS`: Comma-separated directories containing custom tool modules
  - Usage: Lets Hercules load additional tools outside `testzeus_hercules/core/tools`

- `NO_WAIT_FOR_LOAD_STATE`: Skip waiting for Playwright load state in supported browser operations
  - Values: `true`, `false`

## Reliability, Reporting and Strictness Flags

These flags control the core-loop hardening and run-reporting features. Everything
that could change results, exit codes or the files written by a run is **opt-in and
defaults to the previous behaviour**. Boolean flags accept `1`, `true`, `yes` or `on`
(case-insensitive); anything else, including unset, means off.

| Variable | Default | Effect when enabled |
|----------|---------|---------------------|
| `STRICT_EXIT_CODE` | off | The process exits with status `1` when any scenario failed or errored. By default the exit status is `0` regardless of scenario results (`EXIT_CODE_COMPAT=1` still forces this legacy behaviour even when `STRICT_EXIT_CODE` is set). |
| `STRICT_VERDICT` | off | A scenario only counts as passed when it terminated cleanly **and** the planner explicitly recorded `is_passed: true`. By default a scenario without an explicit assertion passes when it terminated, and an asserting scenario passes on a truthy `is_passed`. Missing results and structured execution/cleanup failures are always failures. |
| `ENABLE_OBSERVER_INVARIANTS` | off | After each helper step the proof artifacts are judged and a claimed pass is overruled by HTTP 5xx responses or uncaught console exceptions (`Uncaught`, `TypeError`, `Cannot read`). Off by default because these signals can fail otherwise healthy scenarios. |
| `ENABLE_RUN_REPORTING` | off | Writes `Hercules_Test_Report.md` next to the merged JUnit XML. |
| `ENABLE_FAILURE_BUNDLES` | off | Copies per-failure evidence (screenshots, video, network log) into a `bundles/` directory next to the JUnit XML. |
| `ENABLE_RUN_HISTORY` | off | Records every run and testcase in the `hercules_history.db` run-history database. |
| `ENABLE_FAILURE_CLASSIFICATION` | off | Classifies failed testcases and annotates their bundle and run-history entry (best effort). |
| `ENABLE_SELECTOR_RECORDING` | off | Records observed alternative selectors into the healing selector store (writes `selector_store.json` under `opt/`) and counts failed click selectors there. When off, the store is neither read nor written. Observability only; nothing is auto-healed. |
| `STRICT_AGENTS_LLM_CONFIG` | off | A configured `AGENTS_LLM_CONFIG_FILE` that is missing or cannot be parsed raises an error at startup. By default the problem is logged and the `LLM_MODEL_*` environment configuration is used. |
| `CONFIG_DICT_OVERRIDES_ENV` | off | Values passed programmatically as a config dict win over environment variables. By default environment variables win. Guided and bulk mode always apply their per-run overrides (generated feature file, per-folder project root, input file and test data path) this way, and these per-run overrides also win over CLI flags such as `--project-base`. |
| `HERCULES_DISABLE_CLI_PARSE` | off | Skip parsing the shared Hercules command-line flags. They are parsed for every entrypoint except known subcommand CLIs (`hercules-dashboard`, `hercules-suite`). |

Always on (bug and safety fixes, no flag): cancellation (Ctrl-C, `asyncio.wait_for`) is never
swallowed by the scenario loop, filename sanitisation, network-log and error redaction,
cleanup error aggregation, tool-result size capping and planner failure containment.

Planner guards (always on):

- **Corrective re-prompt.** A malformed planner decision (for example `is_assert` as the string
  `"true"`, or an empty `next_step` while `terminate` is `"no"`) gets one corrective re-prompt that
  names the problem. Only if the second answer is still invalid is the scenario failed.
- **Stall guard.** When the planner returns an empty or unparseable `next_step` three turns in a row,
  the scenario ends as failed ("no executable progress") instead of looping until the
  planner round limit (500 turns). Repeating a step that already ran (for example clicking "Next page"
  several times) counts as progress; it is only logged as `PLANNER_REPEAT_NOTICE`.

The optional mobile (Appium) and healing hooks are inert unless `DEVICE_MANAGER=appium` is
set and the Appium client is installed.

## Configuration Priority

The effective configuration is assembled in this order:

1. Built-in defaults (only fill keys that no other source set)
2. Values passed programmatically as a config dict
3. Process environment variables
4. `.env` values (loaded with `override=True`, so a key in `.env` replaces the same process
   environment variable; `.env` is not loaded when `IS_TEST_ENV=true`)
5. `--config` YAML/JSON file values
6. Command-line arguments

Later sources override earlier sources (set `CONFIG_DICT_OVERRIDES_ENV=true` to move the config dict above the process environment and `.env`). If both `AGENTS_LLM_CONFIG_FILE` and
direct `LLM_MODEL_*` settings are present, the selected runtime path depends on
which CLI/config values are active for that run.

### Dashboard and CLI services
- `DASHBOARD_AUTH_TOKEN`: Token required by `hercules-dashboard` (`Authorization: Bearer <token>`, or HTTP Basic with the token as password so a browser can log in). Mandatory (>= 16 chars) when `--host` is not loopback
- `DASHBOARD_ALLOWED_HOSTS`: Extra comma-separated `Host` header names accepted by the dashboard (loopback names and a specific bind host are always accepted; with a wildcard bind and no list the Host check is skipped and the token is the only gate)

