"""
Code generator integration for converting agent interactions to code mode scripts.
"""

import os
import json
from typing import List, Dict, Any, Optional
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger


class CodeGeneratorIntegration:
    """Integration class for code generation from agent interactions."""
    
    def __init__(self):
        self.interactions_log_path = None
        self._setup_log_path()
    
    def _setup_log_path(self) -> None:
        """Setup the interactions log path."""
        proof_path = get_global_conf().get_proof_path()
        self.interactions_log_path = os.path.join(proof_path, "interaction_logs.ndjson")
    
    def read_interaction_logs(self) -> List[Dict[str, Any]]:
        """Read interaction logs from the browser logger."""
        interactions = []
        
        if not os.path.exists(self.interactions_log_path):
            return interactions
        
        try:
            with open(self.interactions_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            interaction = json.loads(line)
                            interactions.append(interaction)
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Failed to read interaction logs: {e}")
        
        return interactions
    
    def filter_browser_interactions(self, interactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter for browser navigation interactions only."""
        browser_interactions = []
        
        for interaction in interactions:
            if (interaction.get("agent_type") == "browser_nav_agent" and 
                interaction.get("interaction_type") == "selector" and
                interaction.get("success", False)):
                browser_interactions.append(interaction)
        
        return browser_interactions
    
    def generate_python_script(self, interactions: List[Dict[str, Any]], output_filename: str = "generated_test.py") -> str:
        """Generate Python script from interactions."""
        script_lines = [
            "#!/usr/bin/env python3",
            '"""',
            "Generated test script from TestZeus Hercules agent interactions.",
            "This script uses testzeus_hercules_tools in code mode with standard selectors.",
            '"""',
            "",
            "import asyncio",
            "from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager",
            "from testzeus_hercules_tools.tools import click_element, enter_text, hover_element, select_dropdown",
            "",
            "",
            "async def main():",
            "    # Initialize tools in code mode",
            "    config = ToolsConfig(mode='code', headless=False)",
            "    playwright_manager = ToolsPlaywrightManager(config)",
            "    await playwright_manager.initialize()",
            "    page = await playwright_manager.get_page()",
            "    ",
            "    try:",
            "        # Add your page navigation here",
            "        # await page.goto('https://your-target-site.com')",
            "        ",
        ]
        
        for interaction in interactions:
            tool_name = interaction.get("tool_name", "")
            action = interaction.get("action", "")
            selector = interaction.get("selector", "")
            alternative_selectors = interaction.get("alternative_selectors", {})
            additional_data = interaction.get("additional_data", {})
            
            code_selector = self._get_best_code_selector(selector, alternative_selectors)
            
            if tool_name == "click":
                script_lines.extend(self._generate_click_code(code_selector, action, additional_data))
            elif tool_name == "entertext":
                script_lines.extend(self._generate_input_code(code_selector, additional_data))
            elif tool_name == "hover":
                script_lines.extend(self._generate_hover_code(code_selector))
        
        script_lines.extend([
            "        ",
            "    finally:",
            "        await playwright_manager.close()",
            "",
            "",
            "if __name__ == '__main__':",
            "    asyncio.run(main())",
        ])
        
        output_path = os.path.join(get_global_conf().get_proof_path(), output_filename)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(script_lines))
        
        return output_path
    
    def _get_best_code_selector(self, original_selector: str, alternative_selectors: Dict[str, str]) -> str:
        """Get the best selector for code mode."""
        if "css" in alternative_selectors and alternative_selectors["css"].startswith("#"):
            return alternative_selectors["css"]
        
        if "xpath" in alternative_selectors:
            return alternative_selectors["xpath"]
        
        if "aria" in alternative_selectors:
            return alternative_selectors["aria"]
        
        if "css" in alternative_selectors:
            return alternative_selectors["css"]
        
        if original_selector.startswith("[md='") and original_selector.endswith("']"):
            return f"[data-testid='{original_selector[5:-2]}']"  # Convert to data-testid
        
        return original_selector
    
    def _generate_click_code(self, selector: str, action: str, additional_data: Dict[str, Any]) -> List[str]:
        """Generate click code."""
        click_type = additional_data.get("click_type", "click")
        
        lines = [
            f"        # Click action: {action}",
            f"        result = await click_element(",
            f"            selector='{selector}',",
        ]
        
        if click_type != "click":
            lines.append(f"            click_type='{click_type}',")
        
        lines.extend([
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Click result: {{result[\"success\"]}}')  # {action}",
            f"        ",
        ])
        
        return lines
    
    def _generate_input_code(self, selector: str, additional_data: Dict[str, Any]) -> List[str]:
        """Generate input code."""
        text = additional_data.get("text_entered", "")
        
        lines = [
            f"        # Enter text",
            f"        result = await enter_text(",
            f"            selector='{selector}',",
            f"            text='{text}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Input result: {{result[\"success\"]}}')  # Text: {text}",
            f"        ",
        ]
        
        return lines
    
    def _generate_hover_code(self, selector: str) -> List[str]:
        """Generate hover code."""
        lines = [
            f"        # Hover action",
            f"        result = await hover_element(",
            f"            selector='{selector}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Hover result: {{result[\"success\"]}}')  # Hover executed",
            f"        ",
        ]
        
        return lines
    
    def generate_from_current_session(self, output_filename: str = "generated_test.py") -> Optional[str]:
        """Generate code from current session."""
        try:
            interactions = self.read_interaction_logs()
            browser_interactions = self.filter_browser_interactions(interactions)
            
            if not browser_interactions:
                logger.info("No browser interactions found to generate code from")
                return None
            
            output_path = self.generate_python_script(browser_interactions, output_filename)
            logger.info(f"Generated Python script: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to generate code from session: {e}")
            return None
