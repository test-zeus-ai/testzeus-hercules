# TestZeus Hercules Tools

A dual-mode browser automation package that can operate in both agent mode (using md attributes) and code mode (using standard CSS/XPath selectors).

## Features

- **Dual Mode Operation**: Switch between agent mode (md attributes) and code mode (standard selectors)
- **Comprehensive Logging**: Track all interactions with detailed element information
- **Code Generation**: Convert logged agent interactions to standalone Python scripts
- **Browser Automation**: Click, input text, hover, and dropdown selection tools
- **Playwright Integration**: Built on top of Playwright for reliable browser automation

## Installation

```bash
pip install testzeus_hercules_tools
```

## Quick Start

### Agent Mode (Default)

```python
import asyncio
from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
from testzeus_hercules_tools.tools import click_element, enter_text

async def main():
    config = ToolsConfig(mode="agent")
    playwright_manager = ToolsPlaywrightManager(config)
    await playwright_manager.initialize()
    
    # Use md attributes in agent mode
    await click_element("button_123", config=config, playwright_manager=playwright_manager)
    await enter_text("input_456", "Hello World", config=config, playwright_manager=playwright_manager)
    
    await playwright_manager.close()

asyncio.run(main())
```

### Code Mode

```python
import asyncio
from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
from testzeus_hercules_tools.tools import click_element, enter_text

async def main():
    config = ToolsConfig(mode="code")
    playwright_manager = ToolsPlaywrightManager(config)
    await playwright_manager.initialize()
    
    # Use standard selectors in code mode
    await click_element("button.primary", config=config, playwright_manager=playwright_manager)
    await enter_text("#search-input", "Hello World", config=config, playwright_manager=playwright_manager)
    
    await playwright_manager.close()

asyncio.run(main())
```

## Available Tools

- `click_element(selector, click_type="click", wait_before=0.0)` - Click elements
- `enter_text(selector, text, clear_first=True)` - Enter text into input fields
- `hover_element(selector)` - Hover over elements
- `select_dropdown(selector, value, by="value")` - Select dropdown options

## Configuration

```python
from testzeus_hercules_tools import ToolsConfig

config = ToolsConfig(
    mode="agent",  # or "code"
    browser_type="chromium",  # or "firefox", "webkit"
    headless=True,
    browser_resolution="1920,1080",
    enable_logging=True,
    log_path="/path/to/logs"
)
```

## Logging and Code Generation

```python
from testzeus_hercules_tools.tools import InteractionLogger, CodeGenerator

# Enable logging
config = ToolsConfig(enable_logging=True)
logger = InteractionLogger(config)

# After running interactions in agent mode
code_generator = CodeGenerator(config)
interactions = logger.get_successful_interactions()
generated_code = code_generator.generate_code_from_logs(interactions)

# Save generated code
filepath = code_generator.save_generated_code(generated_code, "my_test.py")
```

## Environment Variables

- `TESTZEUS_TOOLS_MODE`: Set to "agent" or "code"
- `BROWSER_TYPE`: Browser type (chromium, firefox, webkit)
- `HEADLESS`: Run in headless mode (true/false)
- `BROWSER_RESOLUTION`: Browser resolution (width,height)
- `ENABLE_LOGGING`: Enable interaction logging (true/false)
- `LOG_PATH`: Path for log files

## Integration with TestZeus Hercules

This package is designed to work alongside TestZeus Hercules, providing the core tools in a reusable format that can be used both by the agent system and in standalone code mode.

## License

Apache License 2.0
