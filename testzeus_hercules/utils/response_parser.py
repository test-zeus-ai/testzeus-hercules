import json
from typing import Any

from testzeus_hercules.utils.logger import logger


def parse_response(message: str) -> dict[str, Any]:
    """
    Parse the response from the browser agent and return the response as a dictionary.
    """
    # Parse the response content
    json_response = {}

    # Check if message is wrapped in ```json ``` blocks
    if "```json" in message:
        start_idx = message.find("```json") + 7
        end_idx = message.find("```", start_idx + 7)
        message = message[start_idx:end_idx]
    else:
        # Original handling for ``` blocks
        if message.startswith("```"):
            message = message[3:]
        if message.endswith("```"):
            message = message[:-3]
        if message.startswith("json"):
            message = message[4:]

    message = message.strip()
    message = message.replace("\\n", "\n")
    message = message.replace("\n", " ")  # type: ignore

    try:
        json_response: dict[str, Any] = json.loads(message)
    except Exception as e:
        # Rest of the error handling remains the same
        logger.warn(f'LLM response was not properly formed JSON. Will try to use it as is. LLM response: "{message}". Error: {e}')

        if "plan" in message and "next_step" in message:
            start = message.index("plan") + len("plan")
            end = message.index("next_step")
            json_response["plan"] = message[start:end].replace('"', "").strip()
        if "next_step" in message and "terminate" in message:
            start = message.index("next_step") + len("next_step")
            end = message.index("terminate")
            json_response["next_step"] = message[start:end].replace('"', "").strip()
        if "terminate" in message and "final_response" in message:
            start = message.index("terminate") + len("terminate")
            end = message.index("final_response")
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

            start = message.index("final_response") + len("final_response")
            end = len(message) - 1
            json_response["final_response"] = message[start:end].replace('"', "").strip()

        elif "terminate" in message:
            start = message.index("terminate") + len("terminate")
            end = len(message) - 1
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

    return json_response
