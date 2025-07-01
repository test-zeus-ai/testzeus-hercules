"""
Code generator for converting logged interactions to Python scripts.
"""

import os
from typing import List, Dict, Any, Optional
from .logger import InteractionLog, InteractionLogger
from ..config import ToolsConfig


class CodeGenerator:
    """Generates Python code from logged interactions."""
    
    def __init__(self, config: Optional[ToolsConfig] = None):
        self.config = config or ToolsConfig.from_env()
    
    def generate_code_from_logs(self, interactions: List[InteractionLog]) -> str:
        """Generate Python code from interaction logs."""
        code_lines = [
            "#!/usr/bin/env python3",
            '"""',
            "Generated test script from testzeus_hercules_tools interactions.",
            "This script uses code mode with CSS/XPath selectors instead of md attributes.",
            '"""',
            "",
            "import asyncio",
            "from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager",
            "from testzeus_hercules_tools.tools import click_element, enter_text, hover_element, select_dropdown",
            "",
            "",
            "async def main():",
            "    # Initialize tools in code mode",
            "    config = ToolsConfig(mode='code')",
            "    playwright_manager = ToolsPlaywrightManager(config)",
            "    await playwright_manager.initialize()",
            "    ",
            "    try:",
        ]
        
        for interaction in interactions:
            if not interaction.success:
                continue
                
            selector = self._get_code_mode_selector(interaction)
            
            if interaction.tool_name == "click_element":
                code_lines.extend(self._generate_click_code(interaction, selector))
            elif interaction.tool_name == "enter_text":
                code_lines.extend(self._generate_input_code(interaction, selector))
            elif interaction.tool_name == "hover_element":
                code_lines.extend(self._generate_hover_code(interaction, selector))
            elif interaction.tool_name == "select_dropdown":
                code_lines.extend(self._generate_dropdown_code(interaction, selector))
        
        code_lines.extend([
            "    ",
            "    finally:",
            "        await playwright_manager.close()",
            "",
            "",
            "if __name__ == '__main__':",
            "    asyncio.run(main())",
        ])
        
        return "\n".join(code_lines)
    
    def _get_code_mode_selector(self, interaction: InteractionLog) -> str:
        """Get the best selector for code mode from interaction log."""
        if not interaction.element_info or not interaction.element_info.get("alternative_selectors"):
            selector = interaction.selector
            if selector.startswith("[md='") and selector.endswith("']"):
                md_value = selector[5:-2]
                return f"[md='{md_value}']"  # Keep md for now, could be improved
            return selector
        
        alt_selectors = interaction.element_info["alternative_selectors"]
        
        if "css" in alt_selectors and alt_selectors["css"].startswith("#"):
            return alt_selectors["css"]
        
        if "xpath" in alt_selectors:
            return alt_selectors["xpath"]
        
        if "aria" in alt_selectors:
            return alt_selectors["aria"]
        
        if "css" in alt_selectors:
            return alt_selectors["css"]
        
        return interaction.selector
    
    def _generate_click_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate click code."""
        click_type = "click"
        if interaction.additional_data and "click_type" in interaction.additional_data:
            click_type = interaction.additional_data["click_type"]
        
        lines = [
            f"        # Click element: {interaction.action}",
            f"        result = await click_element(",
            f"            selector='{selector}',",
        ]
        
        if click_type != "click":
            lines.append(f"            click_type='{click_type}',")
        
        lines.extend([
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Click result: {{result[\"success\"]}}')  # {interaction.message if hasattr(interaction, 'message') else 'Click executed'}",
            f"        ",
        ])
        
        return lines
    
    def _generate_input_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate input code."""
        text = ""
        if interaction.additional_data and "text" in interaction.additional_data:
            text = interaction.additional_data["text"]
        
        lines = [
            f"        # Enter text: {interaction.action}",
            f"        result = await enter_text(",
            f"            selector='{selector}',",
            f"            text='{text}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Input result: {{result[\"success\"]}}')  # Text entered: {text}",
            f"        ",
        ]
        
        return lines
    
    def _generate_hover_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate hover code."""
        lines = [
            f"        # Hover element: {interaction.action}",
            f"        result = await hover_element(",
            f"            selector='{selector}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Hover result: {{result[\"success\"]}}')  # Hover executed",
            f"        ",
        ]
        
        return lines
    
    def _generate_dropdown_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate dropdown selection code."""
        value = ""
        by = "value"
        if interaction.additional_data:
            value = interaction.additional_data.get("value", "")
            by = interaction.additional_data.get("by", "value")
        
        lines = [
            f"        # Select dropdown: {interaction.action}",
            f"        result = await select_dropdown(",
            f"            selector='{selector}',",
            f"            value='{value}',",
            f"            by='{by}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Dropdown result: {{result[\"success\"]}}')  # Selected: {value}",
            f"        ",
        ]
        
        return lines
    
    def save_generated_code(self, code: str, filename: str = "generated_test.py") -> str:
        """Save generated code to file."""
        if self.config.log_path:
            output_dir = self.config.log_path
        else:
            output_dir = os.path.join(os.getcwd(), "testzeus_tools_logs")
        
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(code)
        
        return filepath
    
    def generate_from_logger(self, logger: InteractionLogger, filename: str = "generated_test.py") -> str:
        """Generate code from an InteractionLogger instance."""
        interactions = logger.get_successful_interactions()
        code = self.generate_code_from_logs(interactions)
        return self.save_generated_code(code, filename)
