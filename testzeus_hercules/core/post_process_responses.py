import asyncio
from typing import Any

from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.ui_messagetype import MessageType


def final_reply_callback_user_proxy(
    messages: list[dict[str, Any]],
) -> tuple[bool, Any]:
    """Legacy callback for user-proxy style temination checks."""
    global last_agent_response
    last_message = messages[-1]
    logger.debug(f"Post Process Message (User Proxy):%s ", last_message)
    if last_message.get("content") and "##TERMINATE##" in last_message["content"]:
        last_agent_response = last_message["content"].replace("##TERMINATE##", "").strip()
        if last_agent_response:
            logger.debug("*****Final Reply*****")
            logger.debug(f"Final Response:%s", last_agent_response)
            logger.debug("*********************")
            return True, None
    return False, None


def final_reply_callback_planner_agent(
        message: str, 
        message_type: MessageType = MessageType.STEP, 
        stake_id: str = "", 
        helper_name: str = "", 
        is_assert: bool = False, 
        is_passed: bool = False, 
        assert_summary: str = "", 
        is_terminated: bool = False, 
        is_completed: bool = False, 
        final_response: str = "",
) -> tuple[bool, None]:  
    add_event(EventType.STEP, EventData(detail=message_type.value))
    return False, None  
