import json
import os
import time
from typing import Any, Dict, List, Optional, Union

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger


class BrowserLogger:
    """
    A logger class for tracking interactions across different navigation agents:
    - browser_nav_agent: Browser interactions
    - api_nav_agent: API calls and responses
    - sec_nav_agent: Security testing operations
    - sql_nav_agent: Database operations

    Logs are stored in NDJSON format (Newline Delimited JSON).
    """

    _instance = None

    def __init__(self, proof_path: Optional[str] = None) -> None:
        """Initialize the BrowserLogger."""
        self._log_file: str = ""
        self._proof_path = proof_path
        if self._proof_path:
            self._setup_log_file()

    def _setup_log_file(self) -> None:
        """Set up the log file path in the proofs directory."""
        if not self._proof_path:
            self._proof_path = get_global_conf().get_proof_path()
        self._log_file = os.path.join(self._proof_path, "interaction_logs.ndjson")
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

    @classmethod
    def get_instance(cls, proof_path: Optional[str] = None) -> "BrowserLogger":
        """Get or create a singleton instance of BrowserLogger."""
        if cls._instance is None:
            cls._instance = cls(proof_path)
        return cls._instance

    async def log_interaction(
        self,
        agent_type: str,  # browser_nav_agent, api_nav_agent, sec_nav_agent, sql_nav_agent
        tool_name: str,
        action: str,
        interaction_type: str,
        intent: Optional[str] = None,
        request_data: Optional[Dict[str, Any]] = None,
        response_data: Optional[Dict[str, Any]] = None,
        selector: Optional[str] = None,
        selector_type: Optional[str] = None,
        alternative_selectors: Optional[Dict[str, str]] = None,
        element_attributes: Optional[Dict[str, str]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log any type of interaction to the NDJSON file.

        Args:
            agent_type: Type of agent performing the interaction
            tool_name: Name of the tool performing the interaction
            action: The type of action being performed
            interaction_type: Type of interaction (selector/api/security/sql)
            intent: Description of what the interaction aims to achieve
            request_data: Data sent in the request (for API/SQL operations)
            response_data: Data received in response (for API/SQL operations)
            selector: The selector used (for browser interactions)
            selector_type: Type of selector used (for browser interactions)
            alternative_selectors: Alternative selectors (for browser interactions)
            element_attributes: Element attributes (for browser interactions)
            success: Whether the interaction was successful
            error_message: Error message if the interaction failed
            additional_data: Any additional data to log
        """
        # Check if logging is enabled
        if not get_global_conf().should_enable_browser_logs():
            return

        # Ensure log file is set up
        if not self._log_file:
            self._setup_log_file()

        try:
            log_entry = {
                "timestamp": time.time(),
                "agent_type": agent_type,
                "tool_name": tool_name,
                "action": action,
                "interaction_type": interaction_type,
                "success": success,
            }

            if intent:
                log_entry["intent"] = intent

            if request_data:
                log_entry["request_data"] = request_data

            if response_data:
                log_entry["response_data"] = response_data

            if selector:
                log_entry["selector"] = selector
                log_entry["selector_type"] = selector_type or "custom"
                log_entry["alternative_selectors"] = alternative_selectors or {}
                log_entry["element_attributes"] = element_attributes or {}

            if error_message:
                log_entry["error_message"] = error_message

            if additional_data:
                log_entry["additional_data"] = additional_data

            # Write the log entry as a single line JSON
            line = json.dumps(log_entry, ensure_ascii=False) + "\n"

            with open(self._log_file, "a", encoding="utf-8") as file:
                file.write(line)

        except Exception as e:
            logger.error(f"Failed to write interaction log: {e}")

    async def log_browser_interaction(
        self,
        tool_name: str,
        action: str,
        interaction_type: str,
        selector: Optional[str] = None,
        selector_type: Optional[str] = None,
        alternative_selectors: Optional[Dict[str, str]] = None,
        element_attributes: Optional[Dict[str, str]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a browser interaction."""
        await self.log_interaction(
            agent_type="browser_nav_agent",
            tool_name=tool_name,
            action=action,
            interaction_type=interaction_type,
            selector=selector,
            selector_type=selector_type,
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=success,
            error_message=error_message,
            additional_data=additional_data,
        )

    async def log_api_interaction(
        self,
        tool_name: str,
        action: str,
        intent: str,
        request_data: Dict[str, Any],
        response_data: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an API interaction."""
        await self.log_interaction(
            agent_type="api_nav_agent",
            tool_name=tool_name,
            action=action,
            interaction_type="api",
            intent=intent,
            request_data=request_data,
            response_data=response_data,
            success=success,
            error_message=error_message,
            additional_data=additional_data,
        )

    async def log_security_interaction(
        self,
        tool_name: str,
        action: str,
        intent: str,
        test_type: str,
        target: str,
        findings: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a security testing interaction."""
        await self.log_interaction(
            agent_type="sec_nav_agent",
            tool_name=tool_name,
            action=action,
            interaction_type="security",
            intent=intent,
            request_data={"test_type": test_type, "target": target},
            response_data=findings,
            success=success,
            error_message=error_message,
            additional_data=additional_data,
        )

    async def log_sql_interaction(
        self,
        tool_name: str,
        action: str,
        intent: str,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        result: Optional[Any] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a SQL interaction."""
        await self.log_interaction(
            agent_type="sql_nav_agent",
            tool_name=tool_name,
            action=action,
            interaction_type="sql",
            intent=intent,
            request_data={"query": query, "parameters": parameters},
            response_data={"result": result} if result is not None else None,
            success=success,
            error_message=error_message,
            additional_data=additional_data,
        )

    async def log_selector_interaction(
        self,
        tool_name: str,
        selector: str,
        action: str,
        selector_type: str,
        alternative_selectors: Optional[Dict[str, str]] = None,
        element_attributes: Optional[Dict[str, str]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a selector interaction (legacy method for compatibility)."""
        await self.log_browser_interaction(
            tool_name=tool_name,
            action=action,
            interaction_type="selector",
            selector=selector,
            selector_type=selector_type,
            alternative_selectors=alternative_selectors,
            element_attributes=element_attributes,
            success=success,
            error_message=error_message,
            additional_data=additional_data,
        )

    async def get_alternative_selectors(
        self, element: Any, page: Any
    ) -> Dict[str, str]:
        """Generate alternative selectors for an element."""
        try:
            selectors = {}

            # Get XPath with improved error handling and properly scoped variables
            xpath = await page.evaluate(
                """(element) => {
                try {
                    const getXPath = (elm) => {
                        if (!elm || !elm.nodeType) return null;
                        
                        const segs = [];
                        while (elm && elm.nodeType === 1) {
                            if (elm.hasAttribute('id')) {
                                segs.unshift(`//*[@id="${elm.getAttribute('id')}"]`);
                                return segs.join('/');
                            } else {
                                let sib = elm;
                                let nth = 1;
                                for (sib = sib.previousSibling; sib; sib = sib.previousSibling) {
                                    if (sib.nodeType === 1 && sib.tagName === elm.tagName) nth++;
                                }
                                segs.unshift(elm.tagName.toLowerCase() + '[' + nth + ']');
                            }
                            elm = elm.parentNode;
                        }
                        return segs.length ? '/' + segs.join('/') : null;
                    };
                    return getXPath(element);
                } catch (error) {
                    console.error('XPath generation error:', error);
                    return null;
                }
            }""",
                element,
            )
            if xpath:
                selectors["xpath"] = xpath

            # Get ARIA selector with improved error handling
            aria = await page.evaluate(
                """(element) => {
                try {
                    if (!element || !element.nodeType) return null;
                    
                    if (element.getAttribute('role')) {
                        return `[role="${element.getAttribute('role')}"]`;
                    }
                    if (element.getAttribute('aria-label')) {
                        return `[aria-label="${element.getAttribute('aria-label')}"]`;
                    }
                    return null;
                } catch (error) {
                    console.error('ARIA selector generation error:', error);
                    return null;
                }
            }""",
                element,
            )
            if aria:
                selectors["aria"] = aria

            return selectors
        except Exception as e:
            logger.error(f"Failed to generate alternative selectors: {e}")
            return {}

    async def get_element_attributes(self, element: Any) -> Dict[str, str]:
        """Get relevant attributes from an element."""
        try:
            attributes = {}

            # Get common attributes
            for attr in ["id", "class", "name", "type", "value", "role", "aria-label"]:
                value = await element.get_attribute(attr)
                if value:
                    attributes[attr] = value

            # Get element tag
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            if tag:
                attributes["tag"] = tag

            return attributes
        except Exception as e:
            logger.error(f"Failed to get element attributes: {e}")
            return {}


# Don't create instance at module level
def get_browser_logger(proof_path: Optional[str] = None) -> BrowserLogger:
    """Get the singleton instance of BrowserLogger."""
    return BrowserLogger.get_instance(proof_path)
