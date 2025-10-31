# Python Sandbox Execution Feature

## Overview

Hercules now supports executing custom Python scripts directly from your Gherkin test cases through the **Python Sandbox Execution** feature. This powerful capability allows you to run complex automation workflows, custom business logic, and multi-step operations with full Playwright browser access—all from simple Gherkin steps.

## Why Use Python Sandbox Execution?

### Benefits

- 🎯 **Complex Logic**: Handle sophisticated automation scenarios that are difficult to express in plain Gherkin
- 🔄 **Reusability**: Write once, use across multiple test cases
- 💪 **Full Power**: Access the complete Playwright API, Python ecosystem, and custom modules
- 🛡️ **Secure**: Executes in a controlled sandbox environment with configurable permissions
- 📦 **Multi-tenant**: Different security levels for different types of scripts (executor, data, API, restricted)

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

### 3. Configure Permissions (Optional)

Set the tenant ID to control what modules your scripts can access:

```bash
# Full access (recommended for complex automation)
export SANDBOX_TENANT_ID="executor_agent"

# Or use CLI argument
hercules --sandbox-tenant-id executor_agent --input-file test.feature
```

---

## Available Injections

Scripts automatically have access to these objects and modules:

### Always Available (Base Injections)

```python
# Playwright objects (always available)
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
hercules --sandbox-tenant-id executor_agent --input-file test.feature

# Set custom injections
hercules --sandbox-custom-injections '{"modules": ["jwt"], "custom_objects": {"API_KEY": "xyz"}}' \
         --input-file test.feature
```

### .env File

```env
# Sandbox configuration
SANDBOX_TENANT_ID=executor_agent
SANDBOX_PACKAGES=requests,pandas,numpy
SANDBOX_CUSTOM_INJECTIONS={"modules": ["jwt"], "custom_objects": {"API_KEY": "secret"}}

# Other Hercules config
LLM_MODEL_NAME=gpt-4o
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
And execute script at "scripts/long_process.py" with 600 second timeout
And run the migration script at "migrations/data_migration.py" with 1800 second timeout
```

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
1. Use correct relative path from project root: `opt/scripts/my_script.py`
2. Use absolute path: `/full/path/to/script.py`
3. Check file exists in the correct location

### Timeout Errors

**Error:**
```
Execution timed out after 300 seconds
```

**Solutions:**
1. Increase timeout in Gherkin: `with 600 second timeout`
2. Optimize script (reduce waits, improve selectors)
3. Check for infinite loops or blocking operations

---

## Security Considerations

### Tenant Isolation

Different tenants have different capabilities:
- `executor_agent`: Full access (use carefully)
- `data_agent`: No external API access (safe for data processing)
- `api_agent`: No data processing libraries (safe for API work)
- `restricted_agent`: Minimal access (safest)

### Best Practices

1. **Least Privilege**: Use the most restricted tenant that meets your needs
2. **No Secrets in Scripts**: Use environment variables or custom injections
3. **Validate Inputs**: Always validate function arguments
4. **Error Handling**: Catch and handle exceptions properly
5. **Audit Scripts**: Review scripts before execution

---

## Advanced Usage

### Custom Injections

Inject your own modules and objects:

```bash
export SANDBOX_CUSTOM_INJECTIONS='{
  "modules": ["jwt", "hashlib", "hmac"],
  "custom_objects": {
    "API_KEY": "sk-test-123",
    "API_SECRET": "secret-key",
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
SANDBOX_TENANT_ID=executor_agent hercules --input-file full_test.feature

# Test 2: Data processing only
SANDBOX_TENANT_ID=data_agent hercules --input-file data_test.feature

# Test 3: API testing only
SANDBOX_TENANT_ID=api_agent hercules --input-file api_test.feature
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

Scripts return structured results:
```json
{
  "success": true,
  "result": {
    "status": "success",
    "filter_applied": "Turtle Neck",
    "product_count": 1,
    "message": "Successfully applied Turtle Neck filter. 1 products found."
  },
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
- ✅ **Security**: Multi-tenant isolation and controlled access

Perfect for complex automation scenarios where simple Gherkin steps aren't enough!

For more examples, see the `docs/sandbox_examples/` directory in the repository.

