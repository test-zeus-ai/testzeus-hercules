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
            "from testzeus_hercules_tools.tools import (",
            "    # Browser interaction tools",
            "    click_element, enter_text, hover_element, select_dropdown,",
            "    open_url, press_key_combination, upload_file, get_page_text,",
            "    # Specialized operation tools", 
            "    execute_select_query, http_request, test_page_accessibility,",
            "    wait_for_seconds, wait_until_condition, run_security_scan",
            ")",
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
            elif interaction.tool_name == "execute_select_query":
                code_lines.extend(self._generate_sql_code(interaction, selector))
            elif interaction.tool_name == "http_request":
                code_lines.extend(self._generate_api_code(interaction, selector))
            elif interaction.tool_name == "test_page_accessibility":
                code_lines.extend(self._generate_accessibility_code(interaction, selector))
            elif interaction.tool_name in ["wait_for_seconds", "wait_until_condition"]:
                code_lines.extend(self._generate_wait_code(interaction, selector))
            elif interaction.tool_name == "run_security_scan":
                code_lines.extend(self._generate_security_code(interaction, selector))
        
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
    
    def _generate_sql_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate SQL query execution code."""
        query = ""
        schema_name = ""
        params = {}
        
        if interaction.additional_data:
            query = interaction.additional_data.get("query", "")
            schema_name = interaction.additional_data.get("schema_name", "")
            params = interaction.additional_data.get("params", {})
        
        lines = [
            f"        # Execute SQL query: {interaction.action}",
            f"        result = await execute_select_query(",
            f"            connection_string='{selector}',",
            f"            query='''{query}''',",
        ]
        
        if schema_name:
            lines.append(f"            schema_name='{schema_name}',")
        
        if params:
            lines.append(f"            params={params},")
        
        lines.extend([
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'SQL result: {{result[\"success\"]}} - {{result.get(\"row_count\", 0)}} rows')  # Query executed",
            f"        ",
        ])
        
        return lines
    
    def _generate_api_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate HTTP request code."""
        method = "GET"
        auth_type = None
        body_mode = None
        
        if interaction.additional_data:
            method = interaction.additional_data.get("method", "GET")
            auth_type = interaction.additional_data.get("auth_type")
            body_mode = interaction.additional_data.get("body_mode")
        
        lines = [
            f"        # HTTP request: {interaction.action}",
            f"        result = await http_request(",
            f"            method='{method}',",
            f"            url='{selector}',",
        ]
        
        if auth_type:
            lines.append(f"            auth_type='{auth_type}',")
            lines.append(f"            auth_value='YOUR_AUTH_VALUE',  # Replace with actual auth value")
        
        if body_mode:
            lines.append(f"            body_mode='{body_mode}',")
            lines.append(f"            body={{}},  # Replace with actual request body")
        
        lines.extend([
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'API result: {{result[\"success\"]}} - Status: {{result.get(\"status_code\", \"N/A\")}}')  # {method} request executed",
            f"        ",
        ])
        
        return lines
    
    def _generate_accessibility_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate accessibility test code."""
        violations_count = 0
        
        if interaction.additional_data:
            violations_count = interaction.additional_data.get("violations_count", 0)
        
        lines = [
            f"        # Accessibility test: {interaction.action}",
            f"        result = await test_page_accessibility(",
            f"            page_url='{selector}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Accessibility result: {{result[\"success\"]}} - {{result.get(\"violations_count\", 0)}} violations')  # Accessibility test completed",
            f"        ",
        ]
        
        return lines
    
    def _generate_wait_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate wait operation code."""
        if interaction.tool_name == "wait_for_seconds":
            seconds = float(selector) if selector.replace('.', '').isdigit() else 1.0
            reason = ""
            
            if interaction.additional_data:
                seconds = interaction.additional_data.get("requested_seconds", seconds)
                reason = interaction.additional_data.get("reason", "")
            
            lines = [
                f"        # Wait for seconds: {interaction.action}",
                f"        result = await wait_for_seconds(",
                f"            seconds={seconds},",
            ]
            
            if reason:
                lines.append(f"            reason='{reason}',")
            
            lines.extend([
                f"            config=config,",
                f"            playwright_manager=playwright_manager",
                f"        )",
                f"        print(f'Wait result: {{result[\"success\"]}} - Waited {{result.get(\"actual_duration\", 0):.2f}}s')  # Wait completed",
                f"        ",
            ])
            
        else:  # wait_until_condition
            condition = selector
            max_wait = 30.0
            
            if interaction.additional_data:
                max_wait = interaction.additional_data.get("max_wait_seconds", 30.0)
            
            lines = [
                f"        # Wait until condition: {interaction.action}",
                f"        result = await wait_until_condition(",
                f"            condition_check='{condition}',",
                f"            max_wait_seconds={max_wait},",
                f"            config=config,",
                f"            playwright_manager=playwright_manager",
                f"        )",
                f"        print(f'Condition wait result: {{result[\"success\"]}} - {{result.get(\"elapsed_seconds\", 0):.2f}}s')  # Condition wait completed",
                f"        ",
            ]
        
        return lines
    
    def _generate_security_code(self, interaction: InteractionLog, selector: str) -> List[str]:
        """Generate security scan code."""
        scan_type = "cve"
        vulnerabilities_found = 0
        
        if interaction.additional_data:
            scan_type = interaction.additional_data.get("scan_type", "cve")
            vulnerabilities_found = interaction.additional_data.get("vulnerabilities_found", 0)
        
        lines = [
            f"        # Security scan: {interaction.action}",
            f"        result = await run_security_scan(",
            f"            target_url='{selector}',",
            f"            scan_type='{scan_type}',",
            f"            config=config,",
            f"            playwright_manager=playwright_manager",
            f"        )",
            f"        print(f'Security scan result: {{result[\"success\"]}} - {{result.get(\"vulnerabilities_found\", 0)}} vulnerabilities')  # {scan_type.upper()} scan completed",
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
