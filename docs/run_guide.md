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

2. Run a test:
```bash
testzeus-hercules --project-base=opt
```

Or with individual parameters:
```bash
testzeus-hercules --input-file opt/input/test.feature --output-path opt/output --test-data-path opt/test_data --llm-model gpt-4o --llm-model-api-key your-api-key
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
CDP_ENDPOINT_URL=ws://your-chrome-host:9222
```

This allows Hercules running in Docker to connect to an external browser instance, enabling visible test execution even when running in a container.

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
