from autogen import ConversableAgent
from autogen.agentchat.contrib.capabilities import transform_messages
from autogen.agentchat.contrib.capabilities.text_compressors import LLMLingua
from autogen.agentchat.contrib.capabilities.transforms import TextMessageCompressor
from testzeus_hercules.utils.logger import logger

TEXT_COMPRESSOR_LLM = LLMLingua()
TEXT_COMPRESSOR = TextMessageCompressor(text_compressor=TEXT_COMPRESSOR_LLM)


def add_text_compressor(agent: ConversableAgent) -> None:
    """
    Add a text compressor to the agent
    Args:
        agent (ConversableAgent): The agent that needs text compression in prompts
    """
    context_handling = transform_messages.TransformMessages(
        transforms=[TEXT_COMPRESSOR]
    )
    context_handling.add_to_agent(agent)
    logger.debug(f"Added text compressor to agent: {agent.name}")
