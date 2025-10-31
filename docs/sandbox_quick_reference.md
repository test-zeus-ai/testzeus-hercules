# Python Sandbox - Quick Reference

## Basic Usage

### 1. Write Your Script

**File:** `opt/scripts/my_script.py`

```python
async def my_function(param1: str, param2: int) -> dict:
    """Your automation logic."""
    # page, logger, asyncio automatically available!
    await page.goto("https://example.com")
    
    # Your code here
    
    return {"status": "success", "result": "data"}
```

### 2. Call from Gherkin

```gherkin
And execute the my_function function from script at "scripts/my_script.py" with param1 as "value" and param2 as "123"
```

### 3. Configure Permissions

```bash
export SANDBOX_TENANT_ID="executor_agent"
```

---

## Auto-Injected Objects

These are automatically available in your scripts (no import needed):

```python
# Playwright
page                # Current page
browser             # Browser instance
context             # Browser context
playwright_manager  # Manager instance

# Core
asyncio             # Async operations
logger              # Logger (logger.info, logger.error, etc.)
config              # Global config
os, sys, json, time # Standard library

# Base modules
re                  # Regular expressions
datetime            # Date/time
pathlib             # Path handling
uuid                # UUID generation
```

---

## Tenant Configurations

| Tenant | Available Modules | Use Case |
|--------|------------------|----------|
| `executor_agent` | requests, pandas, numpy, BeautifulSoup | Full automation |
| `data_agent` | pandas, numpy | Data processing only |
| `api_agent` | requests, httpx | API testing only |
| `restricted_agent` | Base only | Minimal access |

### Set Tenant

```bash
# Environment variable
export SANDBOX_TENANT_ID="executor_agent"

# CLI argument
hercules --sandbox-tenant-id executor_agent --input-file test.feature

# .env file
SANDBOX_TENANT_ID=executor_agent
```

---

## Gherkin Patterns

### Execute Script
```gherkin
And execute script at "scripts/my_script.py"
And run the script at "scripts/automation.py"
```

### Call Specific Function
```gherkin
And execute the my_function function from script at "scripts/my_script.py" with arg1 as "value"
And run the process function from script at "scripts/process.py" with id as "123"
```

### With Timeout
```gherkin
And execute script at "scripts/long_script.py" with 600 second timeout
```

---

## Script Templates

### Simple Script
```python
async def main():
    """Main entry point."""
    await page.goto("https://example.com")
    title = await page.title()
    return {"status": "success", "title": title}
```

### Function with Arguments
```python
async def process_data(item_id: str, action: str) -> dict:
    """Process specific item."""
    logger.info(f"Processing {item_id} with {action}")
    
    await page.goto(f"https://example.com/items/{item_id}")
    await page.click(f'button[data-action="{action}"]')
    
    return {"status": "success", "item": item_id}
```

### Error Handling
```python
async def safe_operation():
    """Always handle errors."""
    try:
        result = await page.locator('.element').text_content()
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": "error", "error": str(e)}
```

### Multiple Strategies
```python
async def robust_click(text: str):
    """Try multiple selectors."""
    selectors = [
        f'button:has-text("{text}")',
        f'//button[text()="{text}"]',
        f'input[value="{text}"]'
    ]
    
    for selector in selectors:
        if await page.locator(selector).count() > 0:
            await page.locator(selector).click()
            return {"status": "success", "selector": selector}
    
    raise Exception(f"Element not found: {text}")
```

---

## Configuration Options

### Environment Variables
```bash
SANDBOX_TENANT_ID=executor_agent              # Tenant for module access
SANDBOX_PACKAGES=requests,pandas,numpy        # Additional packages
SANDBOX_CUSTOM_INJECTIONS={"modules": [...]}  # Custom modules/objects
```

### Custom Injections
```bash
export SANDBOX_CUSTOM_INJECTIONS='{
  "modules": ["jwt", "hashlib"],
  "custom_objects": {
    "API_KEY": "secret",
    "MAX_RETRIES": 3
  }
}'
```

---

## Common Patterns

### Wait for Element
```python
await page.wait_for_selector('.my-element', timeout=5000)
```

### Click with Retry
```python
for attempt in range(3):
    try:
        await page.click('.button')
        break
    except:
        await page.wait_for_timeout(1000)
```

### Extract Data
```python
items = await page.locator('.item').all()
data = []
for item in items:
    text = await item.text_content()
    data.append(text.strip())
```

### Fill Form
```python
await page.fill('#name', 'John Doe')
await page.fill('#email', 'john@example.com')
await page.click('#submit')
```

---

## Troubleshooting

### Module Not Found
```python
# Error: NameError: name 'requests' is not defined
# Solution: Set tenant or add to packages
export SANDBOX_TENANT_ID="executor_agent"
```

### Script Not Found
```python
# Error: File not found: my_script.py
# Solution: Use correct path from project root
"scripts/my_script.py"  # ✅ Correct
"my_script.py"          # ❌ Wrong
```

### Timeout
```python
# Error: Execution timed out
# Solution: Increase timeout in Gherkin
And execute script at "script.py" with 600 second timeout
```

---

## File Locations

```
workspace/
├── opt/
│   ├── input/
│   │   └── test.feature          # Your Gherkin tests
│   ├── scripts/                  # Your Python scripts
│   │   ├── filters/
│   │   ├── forms/
│   │   └── validation/
│   ├── proofs/                   # Screenshots saved here
│   │   ├── sandbox_before_*.png
│   │   └── sandbox_after_*.png
│   └── output/                   # Test results
```

---

## Quick Examples

### Example 1: Apply Filter
```python
async def apply_filter(filter_name: str):
    await page.click(f'input[value="{filter_name}"]')
    await page.wait_for_timeout(1000)
    count = await page.locator('.product').count()
    return {"filter": filter_name, "products": count}
```

```gherkin
And execute the apply_filter function from script at "scripts/filters.py" with filter_name as "Turtle Neck"
```

### Example 2: Extract Table
```python
async def extract_table():
    rows = await page.locator('table tr').all()
    data = []
    for row in rows[1:]:  # Skip header
        cells = await row.locator('td').all_text_contents()
        data.append(cells)
    return {"status": "success", "rows": len(data), "data": data}
```

```gherkin
And execute the extract_table function from script at "scripts/extraction.py"
```

### Example 3: Login Flow
```python
async def login(username: str, password: str):
    await page.fill('#username', username)
    await page.fill('#password', password)
    await page.click('#login-button')
    await page.wait_for_selector('.dashboard')
    return {"status": "logged_in", "user": username}
```

```gherkin
And execute the login function from script at "scripts/auth.py" with username as "user@test.com" and password as "secret123"
```

---

## Full Documentation

For complete documentation, examples, and advanced usage:
- [Full Documentation](python_sandbox_execution.md)
- [Example Scripts](sandbox_examples/)

---

## Cheat Sheet

```bash
# Setup
export SANDBOX_TENANT_ID="executor_agent"

# Run test
hercules --input-file test.feature

# Full command
hercules --sandbox-tenant-id executor_agent \
         --input-file test.feature \
         --output-path output/
```

```python
# Script template
async def my_function(param: str) -> dict:
    logger.info(f"Processing {param}")
    await page.goto("https://example.com")
    # Your logic here
    return {"status": "success"}
```

```gherkin
# Gherkin usage
And execute the my_function function from script at "scripts/my_script.py" with param as "value"
```

That's it! You're ready to use Python Sandbox Execution! 🚀

