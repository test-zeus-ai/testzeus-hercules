# Python Sandbox Execution Feature

## Overview

Hercules now supports executing custom Python scripts directly from your Gherkin test cases through the **Python Sandbox Execution** feature. This powerful capability allows you to run complex automation workflows, custom business logic, and multi-step operations with full Playwright browser access—all from simple Gherkin steps.

## Why Use Python Sandbox Execution?

### Benefits

- 🎯 **Complex Logic**: Handle sophisticated automation scenarios that are difficult to express in plain Gherkin
- 🔄 **Reusability**: Write once, use across multiple test cases
- 💪 **Full Power**: Access the complete Playwright API, Python ecosystem, and custom modules
- 🛡️ **Optional containment**: by default (`SANDBOX_MODE=inprocess`) scripts run inside the Hercules process with the live browser objects. With `SANDBOX_MODE=subprocess` each script runs in a disposable child process with CPU/memory limits and a hard timeout. Neither mode is a security boundary — do not run untrusted code.
  Note: in both modes the code sees the operator's environment variables (including model API keys) and the Hercules config — another reason agent-authored code must only be your own.
- 📦 **Multi-tenant**: Different module injection profiles for different types of scripts (executor, data, API, restricted) — a convenience allowlist, not a security control

### When to Use

✅ **Use Python Scripts When:**
- Complex selector strategies with multiple fallback options
- Multi-step workflows with conditional logic
- Custom data processing or validation
- Reusable automation components
- Advanced Playwright features (network interception, multiple pages, etc.)

❌ **Don't Use for:**
- Simple click/type operations (use regular Gherkin steps)
- Basic navigation (use browser helper)
- Single-step interactions

---

## Isolation Model (Important)

The execution mode is chosen by the operator with `SANDBOX_MODE` (config or env):

**`SANDBOX_MODE=inprocess` (default, legacy behaviour)** — the script is `exec`'d
inside the Hercules agent process with the **live** `page`, `browser`, `context` and
`playwright_manager` objects injected:

- **Timeout is cooperative only**: `timeout_seconds` is enforced with `asyncio.wait_for`,
  which can only interrupt the script at an `await`. A blocking call or a CPU-bound loop
  is **not** killed and stalls the agent until it returns.
- **No resource limits**: no CPU/memory rlimits and no process kill; the script shares the
  agent's interpreter, memory and global state.
- **Bounded output**: captured stdout is truncated at 256 KB.

**`SANDBOX_MODE=subprocess` (opt-in)** — each script runs in a **disposable child
process** that Hercules destroys after the run:

- **Hard timeout**: agent-supplied `timeout_seconds` is capped at **300s**; on timeout the child process group is SIGKILLed, so an infinite loop in a script can never freeze the agent.
- **Resource limits (POSIX)**: the child gets a CPU-time limit (timeout + 2s) and a 1 GiB address-space cap.
- **Bounded output**: captured stdout/stderr are truncated at 256 KB per stream.
- **Fresh interpreter per run**: no state leaks between executions, and sandbox output can no longer interleave with the agent's own stdout.
- Live browser objects cannot cross the process boundary; the child only gets a browser when `CDP_ENDPOINT_URL` is configured (it re-attaches over CDP).

**Script path confinement (both modes)**: the script must be a `.py` file that resolves
inside one of the allowed roots — the workspace root (`PROJECT_SOURCE_ROOT`) or the
test-data directory (`TEST_DATA_PATH`). Relative paths are tried under those roots, then
the current working directory; absolute paths (and symlinks) that resolve outside the
roots are rejected.

**This is containment for robustness, not a security boundary.** The executed code runs with a full Python runtime — `os`, `sys`, `__import__`, network and filesystem are all reachable, and injection allowlists are not enforced against imports. Do not run untrusted code through this tool.

---

## Quick Start

### 1. Write Your Python Script

Create a Python script in the `opt/scripts/` directory:

**File: `opt/scripts/apply_filter.py`**

```python
"""Example: Apply a filter on a web page."""

async def apply_filter(filter_type: str) -> dict:
    """
    Apply a filter by clicking the appropriate checkbox.
    
    Args:
        filter_type: The filter option to select (e.g., "Turtle Neck")
        
    Returns:
        Dictionary with execution results
    """
    # Wait for filter section
    await page.wait_for_selector('[data-filter-section="neck"]', timeout=5000)
    
    # Try multiple selector strategies
    selectors = [
        f'input[value="{filter_type}"]',
        f'label:has-text("{filter_type}") input',
        f'//label[contains(text(), "{filter_type}")]/..//input'
    ]
    
    filter_checkbox = None
    for selector in selectors:
        if await page.locator(selector).count() > 0:
            filter_checkbox = page.locator(selector)
            logger.info(f"Found filter using selector: {selector}")
            break
    
    if not filter_checkbox:
        raise Exception(f"Could not find filter checkbox for: {filter_type}")
    
    # Click the checkbox
    await filter_checkbox.click()
    logger.info(f"Clicked filter: {filter_type}")
    
    # Wait for results to update
    await page.wait_for_timeout(2000)
    
    # Count filtered products
    product_count = await page.locator('.product-item').count()
    
    return {
        "status": "success",
        "filter_applied": filter_type,
        "product_count": product_count,
        "message": f"Successfully applied {filter_type} filter. {product_count} products found."
    }
```

**Note:** The `page`, `logger`, `asyncio`, and other common modules are automatically available—no imports needed!

### 2. Use in Your Gherkin Test

Add the execution step to your feature file:

```gherkin
Feature: Product Filtering

Scenario: Apply filter using custom script
    Given a user is on the URL as https://example.com
    When the user searches for "sweater"
    And execute the apply_filter function from script at "scripts/apply_filter.py" with filter_type as "Turtle Neck"
    Then the script should report successful filter application
    And only one product should be displayed
```

### 3. Configure Injections (Optional)

Set the tenant ID to control which modules are *pre-injected* into your scripts. Note: tenants only control the injection convenience allowlist — executed code can always import modules itself.

```bash
# Full access (recommended for complex automation)
export SANDBOX_TENANT_ID="executor_agent"

# Or use CLI argument
testzeus-hercules --sandbox-tenant-id executor_agent --input-file test.feature
```

---

## Available Injections

Scripts automatically have access to these objects and modules:

### Always Available (Base Injections)

```python
# Playwright objects (re-attached inside the child when CDP_ENDPOINT_URL is set;
# without a CDP endpoint, using them raises a clear RuntimeError)
page            # Current Playwright Page instance
browser         # Playwright Browser instance  
context         # Playwright BrowserContext instance
playwright_manager  # PlaywrightManager instance

# Core utilities (always available)
asyncio         # Async operations
logger          # Hercules logger
config          # Global configuration
os, sys, json, time  # Standard library

# Base modules (always available)
re              # Regular expressions
datetime        # Date/time operations
pathlib         # Path handling
uuid            # UUID generation
```

### Tenant-Based Access

Depending on `SANDBOX_TENANT_ID`, additional modules become available:

#### `executor_agent` (Full Access)
```python
requests        # HTTP requests
pandas          # Data analysis
numpy           # Numerical computing
BeautifulSoup   # HTML parsing
hercules_utils  # Project utilities
```

#### `data_agent` (Data Processing Only)
```python
pandas          # Data analysis
numpy           # Numerical computing
# ❌ No requests or external API access
```

#### `api_agent` (API Only)
```python
requests        # HTTP requests
httpx           # Modern async HTTP client
```

#### `restricted_agent` or no tenant (Minimal)
```python
# Only base injections available
```

---

## Configuration

### Environment Variables

```bash
# Set tenant for module access
export SANDBOX_TENANT_ID="executor_agent"

# Add global packages (available to all scripts)
export SANDBOX_PACKAGES="requests,pandas,numpy"

# Add custom modules and objects
export SANDBOX_CUSTOM_INJECTIONS='{"modules": ["jwt"], "custom_objects": {"API_KEY": "xyz"}}'
```

### Command-Line Arguments

```bash
# Set tenant ID
testzeus-hercules --sandbox-tenant-id executor_agent --input-file test.feature

# Set custom injections
testzeus-hercules --sandbox-custom-injections '{"modules": ["jwt"], "custom_objects": {"API_KEY": "xyz"}}' \
         --input-file test.feature
```

### .env File

```env
# Sandbox configuration
SANDBOX_TENANT_ID=executor_agent
SANDBOX_PACKAGES=requests,pandas,numpy
SANDBOX_CUSTOM_INJECTIONS={"modules": ["jwt"], "custom_objects": {"API_KEY": "secret"}}

# Other Hercules config
AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
AGENTS_LLM_CONFIG_FILE_REF_KEY=<provider-key>
HEADLESS=true
```

---

## Script Structure

### Simple Script (Main Function)

```python
"""Simple automation script."""

async def main():
    """Main entry point - automatically called if no function_name specified."""
    await page.goto("https://example.com")
    
    # Your automation logic here
    title = await page.title()
    logger.info(f"Page title: {title}")
    
    return {"status": "success", "title": title}
```

**Gherkin:**
```gherkin
And execute script at "scripts/simple_script.py"
```

### Script with Multiple Functions

```python
"""Script with multiple functions."""

async def login(username: str, password: str):
    """Login function."""
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click("#login-button")
    return {"status": "logged_in", "user": username}

async def navigate_to_products():
    """Navigation function."""
    await page.click("a[href='/products']")
    product_count = await page.locator(".product").count()
    return {"status": "success", "products": product_count}
```

**Gherkin:**
```gherkin
# Call specific function with arguments
And execute the login function from script at "scripts/auth.py" with username as "user@example.com" and password as "secret123"

# Call another function
And execute the navigate_to_products function from script at "scripts/auth.py"
```

### Script with Return Values

```python
"""Extract and return data."""

async def extract_products():
    """Extract product information."""
    products = []
    
    product_elements = await page.locator('.product-item').all()
    
    for product in product_elements:
        name = await product.locator('.product-name').text_content()
        price = await product.locator('.product-price').text_content()
        
        products.append({
            "name": name.strip(),
            "price": price.strip()
        })
    
    logger.info(f"Extracted {len(products)} products")
    
    return {
        "status": "success",
        "products": products,
        "count": len(products)
    }
```

---

## Gherkin Step Patterns

The executor recognizes these patterns in your feature files:

### Basic Execution
```gherkin
And execute script at "scripts/my_script.py"
And run the script at "scripts/automation.py"
And execute the automation script at "workflows/process.py"
```

### Call Specific Function
```gherkin
And execute the apply_filter function from script at "scripts/filters.py" with filter_type as "Turtle Neck"
And run the process_order function from script at "scripts/orders.py" with order_id as "12345"
And call the validate_data function in script at "scripts/validation.py"
```

### With Timeout
```gherkin
And execute script at "scripts/long_process.py" with 300 second timeout
And run the migration script at "migrations/data_migration.py" with 120 second timeout
```

Note: `timeout_seconds` is hard capped at 300 seconds; larger values are clamped down to the cap.

---

## Best Practices

### 1. Script Organization

```
opt/
├── scripts/
│   ├── filters/
│   │   ├── apply_neck_filter.py
│   │   └── apply_price_filter.py
│   ├── forms/
│   │   ├── login_form.py
│   │   └── checkout_form.py
│   └── validation/
│       ├── verify_products.py
│       └── verify_cart.py
```

### 2. Error Handling

```python
async def safe_operation():
    """Always include error handling."""
    try:
        result = await perform_action()
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Operation failed: {e}")
        return {"status": "error", "error": str(e)}
```

### 3. Logging

```python
async def well_logged_function():
    """Use logger for visibility."""
    logger.info("Starting operation")
    logger.debug(f"Processing with params: {params}")
    
    # ... operation ...
    
    logger.info("Operation completed successfully")
    return {"status": "success"}
```

### 4. Multiple Selector Strategies

```python
async def robust_clicking(button_text: str):
    """Try multiple strategies."""
    selectors = [
        f'button:has-text("{button_text}")',
        f'//button[contains(text(), "{button_text}")]',
        f'input[type="button"][value="{button_text}"]',
        f'a:has-text("{button_text}")'
    ]
    
    for selector in selectors:
        if await page.locator(selector).count() > 0:
            await page.locator(selector).click()
            logger.info(f"Clicked using: {selector}")
            return {"status": "success", "selector": selector}
    
    raise Exception(f"Could not find button: {button_text}")
```

---

## Examples

### Example 1: Form Filling with Validation

```python
"""Fill a complex form with validation."""

async def fill_registration_form(user_data: dict):
    """Fill user registration form."""
    
    # Fill text fields
    fields = {
        "#firstName": user_data.get("first_name"),
        "#lastName": user_data.get("last_name"),
        "#email": user_data.get("email"),
        "#phone": user_data.get("phone")
    }
    
    for selector, value in fields.items():
        await page.fill(selector, value)
        logger.info(f"Filled {selector}: {value}")
    
    # Select dropdown
    await page.select_option("#country", user_data.get("country"))
    
    # Check terms checkbox
    await page.check("#terms")
    
    # Submit form
    await page.click("#submit")
    
    # Wait for confirmation
    await page.wait_for_selector(".success-message", timeout=5000)
    
    return {
        "status": "success",
        "message": "Registration form submitted",
        "user": user_data.get("email")
    }
```

### Example 2: Data Extraction and Processing

```python
"""Extract table data and compute statistics."""

async def analyze_price_table():
    """Extract and analyze pricing data."""
    
    # Wait for table
    await page.wait_for_selector("table.price-list")
    
    # Extract rows
    rows = await page.locator("table.price-list tr").all()
    
    prices = []
    for row in rows[1:]:  # Skip header
        cells = await row.locator("td").all_text_contents()
        if len(cells) >= 2:
            price_text = cells[1].replace("$", "").replace(",", "")
            try:
                prices.append(float(price_text))
            except ValueError:
                continue
    
    # Compute statistics
    import numpy as np
    
    result = {
        "status": "success",
        "count": len(prices),
        "average": float(np.mean(prices)),
        "median": float(np.median(prices)),
        "min": float(np.min(prices)),
        "max": float(np.max(prices))
    }
    
    logger.info(f"Analyzed {len(prices)} prices")
    return result
```

### Example 3: Conditional Navigation

```python
"""Navigate based on page state."""

async def smart_navigation():
    """Navigate intelligently based on page state."""
    
    # Check if login required
    if await page.locator('.login-required').count() > 0:
        logger.info("Login required - redirecting")
        await page.click('.login-button')
        return {"status": "redirect", "action": "login"}
    
    # Check if already on target
    if await page.locator('.target-content').is_visible():
        logger.info("Already on target page")
        return {"status": "success", "action": "none"}
    
    # Navigate to target
    await page.click('[href="/target"]')
    await page.wait_for_selector('.target-content')
    
    return {"status": "success", "action": "navigated"}
```

---

## Troubleshooting

### Module Not Available

**Error:**
```
NameError: name 'requests' is not defined
```

**Solutions:**
1. Set tenant ID: `export SANDBOX_TENANT_ID="executor_agent"`
2. Add to packages: `export SANDBOX_PACKAGES="requests"`
3. Use custom injection: `--sandbox-custom-injections '{"modules": ["requests"]}'`

### Script Not Found

**Error:**
```
File not found: scripts/my_script.py
```

**Solutions:**
1. Use a path relative to the workspace root (`PROJECT_SOURCE_ROOT`, e.g. `scripts/my_script.py` for `opt/scripts/my_script.py`), the test-data directory, or the current working directory (`opt/scripts/my_script.py`)
2. An absolute path works only when it lies inside `PROJECT_SOURCE_ROOT` or `TEST_DATA_PATH`; anything else is rejected
3. Check file exists in the correct location

### Timeout Errors

**Error:**
```
Execution timed out after 300 seconds
```

**Solutions:**
1. Increase timeout in Gherkin (hard capped at 300 seconds)
2. Optimize script (reduce waits, improve selectors)
3. Check for infinite loops or blocking operations. With `SANDBOX_MODE=subprocess` the child process is SIGKILLed on timeout, so a stuck script never blocks the agent; in the default in-process mode the timeout only fires at an `await`, so a blocking loop stalls the agent — switch to `SANDBOX_MODE=subprocess` for untrusted-quality or long-running scripts

---

## Security Considerations

### Isolation Model (What It Is — and What It Is Not)

- By default (`SANDBOX_MODE=inprocess`) scripts run **inside the Hercules process** with the live browser objects: there is no kill, no rlimits, and the timeout is cooperative. With `SANDBOX_MODE=subprocess` they run in a **disposable child process** with CPU/memory rlimits and a hard timeout, so a runaway script is killed and cannot hang the agent.
- **It is not a security boundary**: executed code has full Python capabilities (`os`, `sys`, `__import__`, network, filesystem). Tenant policies and `SANDBOX_PACKAGES` are injection *conveniences*, not access controls — scripts can import any installed module themselves.
- **Do not run untrusted code** through this tool. Restrict who can author sandbox scripts in your deployment.

### Script Path Confinement

Script files must be `.py` files that resolve inside the workspace root (`PROJECT_SOURCE_ROOT`) or the test-data directory (`TEST_DATA_PATH`); absolute or symlinked paths outside those roots are rejected.

### Best Practices

1. **No Secrets in Scripts**: Use environment variables or custom injections
2. **Validate Inputs**: Always validate function arguments
3. **Error Handling**: Catch and handle exceptions properly
4. **Audit Scripts**: Review scripts before execution — they run with full Python privileges

---

## Advanced Usage

### Custom Injections

Inject your own modules and objects:

```bash
export SANDBOX_CUSTOM_INJECTIONS='{
  "modules": ["jwt", "hashlib", "hmac"],
  "custom_objects": {
    "API_KEY": "replace-me",
    "API_SECRET": "replace-me",
    "MAX_RETRIES": 3,
    "ENDPOINTS": {
      "prod": "https://api.prod.com",
      "dev": "https://api.dev.com"
    }
  }
}'
```

**In your script:**
```python
# These are automatically available!
token = jwt.encode(payload, API_SECRET)
signature = hashlib.sha256(data).hexdigest()

for attempt in range(MAX_RETRIES):
    response = requests.get(ENDPOINTS["prod"])
    # ...
```

### Multiple Tenant Setup

Different tests can use different tenants:

```bash
# Test 1: Full automation
SANDBOX_TENANT_ID=executor_agent testzeus-hercules --input-file full_test.feature

# Test 2: Data processing only
SANDBOX_TENANT_ID=data_agent testzeus-hercules --input-file data_test.feature

# Test 3: API testing only
SANDBOX_TENANT_ID=api_agent testzeus-hercules --input-file api_test.feature
```

---

## Output and Proofs

### Screenshots

Sandbox execution automatically captures screenshots:
- **Before execution**: `opt/proofs/sandbox_before_<timestamp>.png`
- **After execution**: `opt/proofs/sandbox_after_<timestamp>.png`

### Logs

All execution details are logged:
```
[2025-10-31 22:46:13] INFO - Using sandbox tenant: executor_agent
[2025-10-31 22:46:13] INFO - Resolved file path: opt/scripts/apply_neck_filter.py
[2025-10-31 22:46:13] INFO - Screenshot before execution: opt/proofs/sandbox_before_1761930973.png
[2025-10-31 22:46:15] INFO - Clicked filter: Turtle Neck
[2025-10-31 22:46:17] INFO - Successfully applied filter. 1 products found.
[2025-10-31 22:46:17] INFO - Sandbox execution completed in 3.24s
```

### Execution Results

Scripts return structured results (child stdout/stderr are captured and truncated at 256 KB per stream):
```json
{
  "success": true,
  "result": {
    "status": "success",
    "filter_applied": "Turtle Neck",
    "product_count": 1,
    "message": "Successfully applied Turtle Neck filter. 1 products found."
  },
  "stdout": "...\n",
  "stderr": "...\n",
  "execution_time": 3.245,
  "screenshots": {
    "before": "opt/proofs/sandbox_before_1761930973.png",
    "after": "opt/proofs/sandbox_after_1761930975.png"
  }
}
```

---

## Reference

### Complete Configuration Options

```bash
# Tenant ID (determines module access)
SANDBOX_TENANT_ID=executor_agent|data_agent|api_agent|restricted_agent

# Global packages (comma-separated)
SANDBOX_PACKAGES=requests,pandas,numpy,beautifulsoup4

# Custom injections (JSON string)
SANDBOX_CUSTOM_INJECTIONS={"modules": ["jwt"], "custom_objects": {"KEY": "value"}}
```

### Gherkin Keywords

Keywords that trigger the executor agent:
- `execute`
- `run`
- `call`
- `script`
- `function from script`
- `automation`
- `workflow`

---

## Summary

The Python Sandbox Execution feature gives you:

- 🎯 **Precision**: Exact control over complex interactions
- 🔄 **Reusability**: Scripts work across multiple tests  
- 🛠️ **Maintainability**: Easy to update and debug
- 💪 **Power**: Full Playwright + Python capabilities
- 🧱 **Optional containment**: `SANDBOX_MODE=subprocess` gives a disposable child process per run with resource limits and a hard timeout (the default in-process mode has neither) — a robustness boundary, not a security boundary

Perfect for complex automation scenarios where simple Gherkin steps aren't enough!

For a compact command and pattern summary, see `docs/sandbox_quick_reference.md`.
