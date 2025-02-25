import copy
import json
import tempfile
from typing import Any, Dict, List, Optional, Union

import autogen
from autogen import ConversableAgent, OpenAIWrapper
from autogen.agentchat.agent import Agent
from autogen.agentchat.contrib.capabilities.teachability import Teachability
from autogen.agentchat.contrib.img_utils import (
    gpt4v_formatter,
    message_formatter_pil_to_b64,
)
from autogen.code_utils import content_str

# from autogen.agentchat.contrib.multimodal_conversable_agent import (
#     MultimodalConversableAgent,
# )
from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.response_parser import parse_response

DEFAULT_LMM_SYS_MSG = """You are a helpful AI assistant."""
DEFAULT_MODEL = "gpt-4o"


class MultimodalConversableAgent(ConversableAgent):
    DEFAULT_CONFIG = {
        "model": DEFAULT_MODEL,
    }

    def __init__(
        self,
        name: str,
        system_message: Optional[Union[str, list]] = DEFAULT_LMM_SYS_MSG,
        is_termination_msg: str = None,
        *args: Any,
        **kwargs: Any,
    ):
        super().__init__(
            name, system_message, is_termination_msg=is_termination_msg, *args, **kwargs
        )
        # call the setter to handle special format.
        self.update_system_message(system_message)
        self._is_termination_msg = (
            is_termination_msg
            if is_termination_msg is not None
            else (lambda x: content_str(x.get("content")) == "TERMINATE")
        )

        # Override the `generate_oai_reply`
        self.replace_reply_func(
            ConversableAgent.generate_oai_reply,
            MultimodalConversableAgent.generate_oai_reply,
        )
        self.replace_reply_func(
            ConversableAgent.a_generate_oai_reply,
            MultimodalConversableAgent.a_generate_oai_reply,
        )

    def update_system_message(self, system_message: Union[dict, list, str]) -> None:
        self._oai_system_message[0]["content"] = self._message_to_dict(system_message)[
            "content"
        ]
        self._oai_system_message[0]["role"] = "system"

    @staticmethod
    def _message_to_dict(message: Union[dict, list, str]) -> dict:
        """Convert a message to a dictionary. This implementation
        handles the GPT-4V formatting for easier prompts.

        The message can be a string, a dictionary, or a list of dictionaries:
            - If it's a string, it will be cast into a list and placed in the 'content' field.
            - If it's a list, it will be directly placed in the 'content' field.
            - If it's a dictionary, it is already in message dict format. The 'content' field of this dictionary
            will be processed using the gpt4v_formatter.
        """
        if isinstance(message, str):
            return {"content": gpt4v_formatter(message, img_format="pil")}
        if isinstance(message, list):
            return {"content": message}
        if isinstance(message, dict):
            assert "content" in message, "The message dict must have a `content` field"
            if isinstance(message["content"], str):
                message = copy.deepcopy(message)
                message["content"] = gpt4v_formatter(
                    message["content"], img_format="pil"
                )
            try:
                content_str(message["content"])
            except (TypeError, ValueError) as e:
                print(
                    "The `content` field should be compatible with the content_str function!"
                )
                raise e
            return message
        raise ValueError(f"Unsupported message type: {type(message)}")

    def generate_oai_reply(
        self,
        messages: Optional[list[dict]] = None,
        sender: Optional[Agent] = None,
        config: Optional[OpenAIWrapper] = None,
    ) -> tuple[bool, Union[str, dict, None]]:
        """Generate a reply using autogen.oai.
        Overrides ConversableAgent.generate_oai_reply to handle multimodal messages."""
        client = self.client if config is None else config
        if client is None:
            return False, None
        if messages is None:
            messages = self._oai_messages[sender]

        # Convert PIL images to base64 for all messages
        messages_with_b64_img = message_formatter_pil_to_b64(
            self._oai_system_message + messages
        )

        # Use the base class's _generate_oai_reply_from_client method since it handles tool calls properly
        extracted_response = self._generate_oai_reply_from_client(
            llm_client=client, messages=messages_with_b64_img, cache=self.client_cache
        )

        return (
            (False, None) if extracted_response is None else (True, extracted_response)
        )

    async def a_generate_oai_reply(
        self,
        messages: Optional[list[dict]] = None,
        sender: Optional[Agent] = None,
        config: Optional[OpenAIWrapper] = None,
    ) -> tuple[bool, Union[str, dict, None]]:
        """Generate a reply using autogen.oai asynchronously.
        Overrides ConversableAgent.a_generate_oai_reply to handle multimodal messages.
        """
        from autogen.io import IOStream

        iostream = IOStream.get_default()

        def _generate_oai_reply(
            self, iostream: IOStream, *args: Any, **kwargs: Any
        ) -> tuple[bool, Union[str, dict, None]]:
            with IOStream.set_default(iostream):
                return self.generate_oai_reply(*args, **kwargs)

        import asyncio
        import functools

        return await asyncio.get_event_loop().run_in_executor(
            None,
            functools.partial(
                _generate_oai_reply,
                self=self,
                iostream=iostream,
                messages=messages,
                sender=sender,
                config=config,
            ),
        )


def convert_model_config_to_autogen_format(
    model_config: dict[str, str]
) -> list[dict[str, Any]]:
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


def is_agent_planner_termination_message(
    x: dict[str, str], final_response_callback: callable = None
) -> bool:
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
            content_json = json.loads(
                content.replace("```json", "").replace("```", "").strip()
            )
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
    llm_config: Optional[Dict[str, Any]] = None,
) -> MultimodalConversableAgent:
    """Create a multimodal conversable agent as a singleton.

    Args:
        name: Agent name
        llm_config: LLM configuration
        system_message: System prompt message

    Returns:
        MultimodalConversableAgent instance
    """

    # Singleton instance variable
    if not hasattr(create_multimodal_agent, "_instance"):
        # Get the LLM config for the image comparison agent
        _mca_agent_config = AgentsLLMConfig().get_helper_agent_config()
        _llm_config = [llm_config] or convert_model_config_to_autogen_format(
            _mca_agent_config["model_config_params"]
        )
        if _llm_config:
            _llm_config = _llm_config[0]

        create_multimodal_agent._instance = MultimodalConversableAgent(
            name=name,
            max_consecutive_auto_reply=1,
            human_input_mode="NEVER",
            llm_config=_llm_config,
            system_message=system_message,
        )
    return create_multimodal_agent._instance


def create_user_proxy(
    name: str,
    is_termination_msg: callable,
    max_consecutive_replies: int,
    human_input_mode: str = "NEVER",
    **kwargs: Any,
) -> Agent:
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
    return autogen.UserProxyAgent(
        name=name,
        is_termination_msg=is_termination_msg,
        human_input_mode=human_input_mode,
        max_consecutive_auto_reply=max_consecutive_replies,
        **kwargs,
    )


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
            logger.debug(
                f"Failed to decode JSON: {content}, keeping as multiline string"
            )
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


class ZeusTeachability(Teachability):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)

    def process_last_received_message(self, text: Union[dict[str, Any], str]) -> str:
        """Appends any relevant memos to the message text, and stores any apparent teachings in new memos.
        Uses TextAnalyzerAgent to make decisions about memo storage and retrieval.
        """
        # Try to retrieve relevant memos from the DB.
        expanded_text = ""
        if self.memo_store.last_memo_id > 0:
            expanded_text = self._consider_memo_retrieval(text)

        # Return the (possibly) expanded message text.
        return expanded_text

    def _consider_memo_storage(self, comment: Union[dict[str, Any], str]) -> None:
        """Decides whether to store something from one user comment in the DB."""
        memo_added = False

        # Yes. What question would this information answer?
        question = self._in_analyze(
            comment,
            "Imagine that the user forgot this information in the TEXT. How would they ask you for this information? Include no other text in your response.",
        )
        # Extract the information.
        answer = self._in_analyze(
            comment,
            "Copy the information from the TEXT that should be committed to memory. Add no explanation.",
        )

        self.memo_store.add_input_output_pair(question, answer)
        memo_added = True

        # Were any memos added?
        if memo_added:
            # Yes. Save them to disk.
            self.memo_store._save_memos()

    def _in_analyze(
        self,
        text_to_analyze: Union[dict[str, Any], str],
        analysis_instructions: Union[dict[str, Any], str],
    ) -> str:
        """Asks TextAnalyzerAgent to analyze the given text according to specific instructions."""
        self.analyzer.reset()  # Clear the analyzer's list of messages.
        self.teachable_agent.send(
            recipient=self.analyzer,
            message=text_to_analyze,
            request_reply=False,
            silent=(self.verbosity < 2),
        )  # Put the message in the analyzer's list.
        self.teachable_agent.send(
            recipient=self.analyzer,
            message=analysis_instructions,
            request_reply=False,
            silent=(self.verbosity < 2),
        )  # Request the reply.
        return self.teachable_agent.last_message(self.analyzer)["content"]
