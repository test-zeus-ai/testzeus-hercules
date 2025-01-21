import json
import tempfile
from typing import Any, Dict, List, Optional

import autogen
from autogen.agentchat.agent import Agent
from autogen.agentchat.contrib.multimodal_conversable_agent import (
    MultimodalConversableAgent,
)
from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.response_parser import parse_response


def convert_model_config_to_autogen_format(model_config: dict[str, str]) -> list[dict[str, Any]]:
    """Convert model configuration to Autogen format.

    Args:
        model_config: Raw model configuration dictionary

    Returns:
        List of configuration dictionaries in Autogen format
    """
    env_var: list[dict[str, str]] = [model_config]
    with tempfile.NamedTemporaryFile(delete=False, mode="w") as temp:
        json.dump(env_var, temp)
        temp_file_path = temp.name

    return autogen.config_list_from_json(env_or_file=temp_file_path)


def is_agent_planner_termination_message(x: dict[str, str], final_response_callback: callable = None) -> bool:
    """Check if a message should terminate the planner agent conversation.

    Args:
        x: Message dictionary
        final_response_callback: Optional callback for final response

    Returns:
        bool: True if conversation should terminate
    """
    should_terminate = False
    function: Any = x.get("function", None)
    if function is not None:
        return False

    content: Any = x.get("content", "")
    if content is None:
        content = ""
        should_terminate = True
    else:
        try:
            content_json = json.loads(content.replace("```json", "").replace("```", "").strip())
            _terminate = content_json.get("terminate", "no")
            final_response = content_json.get("final_response", None)
            if _terminate == "yes":
                should_terminate = True
                if final_response and final_response_callback:
                    final_response_callback(final_response)
        except json.JSONDecodeError:
            should_terminate = True

    return should_terminate


def create_multimodal_agent(
    name: str,
    system_message: str = "You are a multimodal conversable agent.",
    llm_config: Optional[List[Dict[str, Any]]] = None,
) -> MultimodalConversableAgent:
    """Create a multimodal conversable agent.

    Args:
        name: Agent name
        llm_config: LLM configuration
        system_message: System prompt message

    Returns:
        MultimodalConversableAgent instance
    """

    # Get the LLM config for the image comparison agent
    _mca_agent_config = AgentsLLMConfig().get_nav_agent_config()
    _llm_config = llm_config or convert_model_config_to_autogen_format(_mca_agent_config["model_config_params"])
    if _llm_config:
        _llm_config = _llm_config[0]
    return MultimodalConversableAgent(name=name, max_consecutive_auto_reply=1, human_input_mode="NEVER", llm_config=_llm_config, system_message=system_message)


def create_user_proxy(name: str, is_termination_msg: callable, max_consecutive_replies: int, human_input_mode: str = "NEVER", **kwargs: Any) -> Agent:
    """Create a user proxy agent with common configurations.

    Args:
        name: Agent name
        is_termination_msg: Termination check function
        max_consecutive_replies: Max consecutive auto-replies
        human_input_mode: Human input mode
        **kwargs: Additional arguments for UserProxyAgent

    Returns:
        UserProxyAgent instance
    """
    return autogen.UserProxyAgent(name=name, is_termination_msg=is_termination_msg, human_input_mode=human_input_mode, max_consecutive_auto_reply=max_consecutive_replies, **kwargs)


def process_chat_message_content(content: Any) -> Any:
    """Process and parse chat message content.

    Args:
        content: Raw message content

    Returns:
        Processed content (dict, str, or original content)
    """
    if isinstance(content, str):
        content = content.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            logger.debug(f"Failed to decode JSON: {content}, keeping as multiline string")
            return content
    elif isinstance(content, (dict, list)):
        return content
    return content


def extract_target_helper(message: str) -> Optional[str]:
    """Extract target helper from message.

    Args:
        message: Message containing target helper tag

    Returns:
        Extracted target helper or None
    """
    try:
        target_helper = message.split("##target_helper: ")[-1].split("##")[0].strip()
        return target_helper if target_helper != "Not_Applicable" else None
    except:
        return None


def parse_agent_response(content: str) -> Dict[str, Any]:
    """Parse agent response and extract key fields.

    Args:
        content: Raw response content

    Returns:
        Dict containing parsed fields like next_step, plan etc.
    """
    try:
        content_json = parse_response(content)
        return {
            "next_step": content_json.get("next_step"),
            "plan": content_json.get("plan"),
            "target_helper": content_json.get("target_helper", "Not_Applicable"),
            "terminate": content_json.get("terminate", "no"),
            "final_response": content_json.get("final_response"),
        }
    except:
        logger.error(f"Failed to parse agent response: {content}")
        return {}


def format_plan_steps(plan: list[str]) -> str:
    """Format plan steps with numbering.

    Args:
        plan: List of plan steps

    Returns:
        Formatted plan string with numbered steps
    """
    return "\n".join([f"{idx+1}. {step}" for idx, step in enumerate(plan)])
