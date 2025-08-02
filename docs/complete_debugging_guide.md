# Complete Debugging Guide for testzeus-hercules

This comprehensive guide covers both setting up testzeus-hercules for local development and debugging specific functionality.

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Setup for Local Development](#setup-for-local-development)
3. [Key Debugging Points](#key-debugging-points)
4. [VS Code Debugging Setup](#vs-code-debugging-setup)
5. [Common Debugging Scenarios](#common-debugging-scenarios)
6. [Troubleshooting](#troubleshooting)
7. [Quick Reference](#quick-reference)

---

## Prerequisites

- Python 3.10 or higher (but less than 3.13)
- Git
- Access to the testzeus-hercules repository
- VS Code with Python extension

---

## Setup for Local Development

### 1. Clone the Repository

```bash
git clone https://github.com/testzeus/testzeus-hercules.git
cd testzeus-hercules
```

### 2. Create a Virtual Environment

```bash
# Create a virtual environment
python3 -m venv testzeus-env

# Activate the virtual environment
# On macOS/Linux:
source testzeus-env/bin/activate

# On Windows:
# testzeus-env\Scripts\activate
```

### 3. Install in Editable Mode

This is the key step that links the installed package to your source code:

```bash
# Make sure you're in the project root directory
pip install -e .
```

**What this does:**
- Installs the package in "editable" mode
- Links the installed package directly to your source code
- Any changes you make to the source code will be immediately available

**Important Note:** The project requires specific dependency versions. On Intel Macs, you may need to ensure `torch = "2.2.2"` is installed. Other platforms may have different torch version requirements.

### 4. Install Playwright Dependencies

```bash
playwright install --with-deps
```

### 5. Verify the Setup

```bash
# Check that the package is installed in editable mode
pip show testzeus-hercules

# You should see something like:
# Location: /path/to/testzeus-hercules/testzeus-env/lib/python3.11/site-packages
# Editable project location: /path/to/testzeus-hercules

# Test that the package points to your source code
python -c "import testzeus_hercules; print('Package location:', testzeus_hercules.__file__)"
# Should show: /path/to/testzeus-hercules/testzeus_hercules/__init__.py
```

### 6. Environment Variables for Development

Set these environment variables for better debugging:

```bash
export HEADLESS=false  # See browser actions
export BROWSER_TYPE=chromium
export RECORD_VIDEO=true
export TAKE_SCREENSHOTS=true
```

---

## Key Debugging Points

### 1. Main Entry Point
**File:** `testzeus_hercules/__main__.py`
```python
def main():
    # Set breakpoint here to see command line arguments
    # Inspect: args.input_file, args.test_data_path, args.output_path
```

### 2. Feature File Processing
**File:** `testzeus_hercules/core/runner.py`
```python
def run_feature_file():
    # Set breakpoint here to see feature file parsing
    # Inspect: feature file content, scenarios, steps
```

### 3. Browser Navigation Agent
**File:** `testzeus_hercules/core/agents/browser_nav_agent.py`
```python
def execute_step(self, step):
    # Set breakpoint here to see each step execution
    # Inspect: step.action, step.selector, step.value
```

### 4. Hover Functionality
**File:** `testzeus_hercules/core/tools/hover.py`
```python
def hover_using_selector(page, selector, **kwargs):
    # Set breakpoint here to debug hover issues
    # Inspect: selector, page.url, element visibility
```

### 5. Click Functionality
**File:** `testzeus_hercules/core/tools/click_using_selector.py`
```python
def click_using_selector(page, selector, **kwargs):
    # Set breakpoint here to debug click issues
    # Inspect: selector, element state, click coordinates
```

### 6. Text Input
**File:** `testzeus_hercules/core/tools/enter_text_using_selector.py`
```python
def enter_text_using_selector(page, selector, text, **kwargs):
    # Set breakpoint here to debug text input issues
    # Inspect: selector, text value, field state
```

### 7. Dropdown Selection
**File:** `testzeus_hercules/core/tools/dropdown_using_selector.py`
```python
def dropdown_using_selector(page, selector, value, **kwargs):
    # Set breakpoint here to debug dropdown issues
    # Inspect: selector, value, available options
```

### 8. Page Navigation
**File:** `testzeus_hercules/core/tools/open_url.py`
```python
def open_url(page, url, **kwargs):
    # Set breakpoint here to debug navigation issues
    # Inspect: url, page load state, redirects
```

### 9. Configuration Loading
**File:** `testzeus_hercules/config.py`
```python
def load_config():
    # Set breakpoint here to see configuration loading
    # Inspect: environment variables, config values
```

### 10. LLM Agent Communication
**File:** `testzeus_hercules/core/agents_llm_config.py`
```python
def get_llm_response():
    # Set breakpoint here to debug LLM communication
    # Inspect: prompts, responses, model configuration
```

---

## VS Code Debugging Setup

### 1. Required VS Code Extensions
Install these extensions for optimal debugging:
- **Python** (`ms-python.python`) - Core Python support
- **Python Debugger** (`ms-python.debugpy`) - Enhanced debugging
- **Python Test Explorer** (`littlefoxteam.vscode-python-test-adapter`) - Test debugging

### 2. VS Code Settings Configuration
Create `.vscode/settings.json`:
```json
{
    "python.defaultInterpreterPath": "./testzeus-env/bin/python",
    "python.terminal.activateEnvironment": true,
    "python.linting.enabled": true,
    "python.linting.flake8Enabled": true,
    "python.formatting.provider": "black",
    "python.sortImports.args": ["--profile", "black"],
    "editor.formatOnSave": true,
    "python.testing.pytestEnabled": true,
    "python.testing.unittestEnabled": false,
    "python.testing.pytestArgs": ["tests"],
    "debug.console.fontSize": 12,
    "debug.console.lineHeight": 18
}
```

### 3. Launch Configuration
The `.vscode/launch.json` file is already configured with:
- **Debug testzeus-hercules command** - Debug your exact command
- **Debug testzeus-hercules (interactive)** - Debug interactive mode

**Current launch.json configuration:**
```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug testzeus-hercules command",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/testzeus_hercules/__main__.py",
            "args": [
                "--input-file", "project_base/input/route_robust.feature",
                "--test-data-path", "project_base/test_data",
                "--output-path", "project_base/output"
            ],
            "console": "integratedTerminal",
            "cwd": "${workspaceFolder}",
            "env": {
                "PYTHONPATH": "${workspaceFolder}",
                "HEADLESS": "false"
            },
            "justMyCode": false,
            "stopOnEntry": false
        },
        {
            "name": "Debug testzeus-hercules (interactive)",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/testzeus_hercules/interactive.py",
            "console": "integratedTerminal",
            "cwd": "${workspaceFolder}",
            "env": {
                "PYTHONPATH": "${workspaceFolder}",
                "HEADLESS": "false"
            },
            "justMyCode": false
        }
    ]
}
```

---

## Common Debugging Scenarios

| Function | File | What to Inspect |
|----------|------|-----------------|
| **Hover** | `testzeus_hercules/core/tools/hover.py` | `selector`, `page.url`, element visibility |
| **Click** | `testzeus_hercules/core/tools/click_using_selector.py` | `selector`, element state, click coordinates |
| **Text Input** | `testzeus_hercules/core/tools/enter_text_using_selector.py` | `selector`, `text`, field state |
| **Step Execution** | `testzeus_hercules/core/agents/browser_nav_agent.py` | `step.action`, `step.selector`, `step.value` |

**Debug Console Commands:**
```python
page.locator(selector).is_visible()  # Check if element exists
page.locator(selector).count()       # Count matching elements
page.url                             # Current page URL
```

---

## Troubleshooting

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Changes not reflected | Check virtual environment, verify editable installation |
| Import errors | Ensure in project root, activate virtual environment |
| Dependency conflicts | Check torch version, reinstall with `pip install -e .` |
| Python 3.13+ | Use pyenv: `pyenv install 3.11.13 && pyenv local 3.11.13` |

### Common testzeus-hercules Issues

| Issue | Debug Steps |
|-------|-------------|
| **Hover Not Working** | 1. Set breakpoint in `hover_using_selector()`<br>2. Check selector value<br>3. Test: `page.locator(selector).is_visible()`<br>4. Verify page URL |
| **Element Not Found** | 1. Set breakpoint in click/hover function<br>2. Check selector value<br>3. Test: `page.locator(selector).count()`<br>4. Verify page loaded |
| **Step Execution Failing** | 1. Set breakpoint in `execute_step()`<br>2. Inspect step object<br>3. Check `step.action` and `step.selector`<br>4. Verify feature file parsing |

---

## Quick Reference

### Advanced Debugging Tips

| Technique | Usage |
|-----------|-------|
| **Conditional Breakpoints** | Right-click breakpoint → "Edit Breakpoint" → `selector == "Transfer"` |
| **Watch Expressions** | Add to Watch panel: `page.url`, `selector`, `page.locator(selector).is_visible()` |
| **Debug Console Commands** | `page.locator(selector).is_visible()`, `page.locator(selector).count()`, `page.url` |

 