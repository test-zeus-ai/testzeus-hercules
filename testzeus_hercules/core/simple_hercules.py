import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import autogen
from autogen import ConversableAgent
from testzeus_hercules.config import SingletonConfigManager, get_global_conf
from testzeus_hercules.core.agents.base_agent import BaseAgent
from testzeus_hercules.core.device_manager import DeviceManager
from testzeus_hercules.core.memory.memory_manager import MemoryManager
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.utils.logger import logger


class SimpleHercules:
    """
    A simplified version of the Hercules test automation system.
    This class provides a streamlined interface for running automated tests
    using a combination of LLM agents and device automation.
    """

    def __init__(
        self,
        planner_max_chat_round: int = 500,
        nav_max_chat_round: int = 10,
        dont_terminate_device_after_run: bool = False,
        command: Optional[str] = None,
        stake_id: Optional[str] = None,
    ):
        """
        Initialize the SimpleHercules instance.

        Args:
            planner_max_chat_round: Maximum number of chat rounds for the planner agent
            nav_max_chat_round: Maximum number of chat rounds for the navigation agent
            dont_terminate_device_after_run: Whether to keep the device open after run
            command: Optional command to execute
            stake_id: Optional stake ID for device management
        """
        self.planner_max_chat_round = planner_max_chat_round
        self.nav_max_chat_round = nav_max_chat_round
        self.dont_terminate_device_after_run = dont_terminate_device_after_run
        self.command: Optional[str] = command
        self.memory: MemoryManager = MemoryManager()
        self.agents_map: Dict[str, ConversableAgent] = {}
        self.device_manager: DeviceManager = DeviceManager(stake_id=stake_id)

    @classmethod
    def create(
        cls,
        planner_max_chat_round: int = 500,
        nav_max_chat_round: int = 10,
        dont_terminate_device_after_run: bool = False,
        command: Optional[str] = None,
        stake_id: Optional[str] = None,
    ) -> "SimpleHercules":
        """
        Create a new instance of SimpleHercules.

        Args:
            planner_max_chat_round: Maximum number of chat rounds for the planner agent
            nav_max_chat_round: Maximum number of chat rounds for the navigation agent
            dont_terminate_device_after_run: Whether to keep the device open after run
            command: Optional command to execute
            stake_id: Optional stake ID for device management

        Returns:
            A new SimpleHercules instance
        """
        instance = cls(
            planner_max_chat_round=planner_max_chat_round,
            nav_max_chat_round=nav_max_chat_round,
            dont_terminate_device_after_run=dont_terminate_device_after_run,
            command=command,
            stake_id=stake_id,
        )
        instance.initialize()
        return instance

    def initialize(self) -> None:
        """Initialize the SimpleHercules instance."""
        self.agents_map = self.__initialize_agents()

    def __initialize_agents(self) -> Dict[str, ConversableAgent]:
        """
        Initialize the agents used by SimpleHercules.

        Returns:
            A dictionary mapping agent names to their instances
        """
        agents_map: Dict[str, ConversableAgent] = {}
        config: SingletonConfigManager = get_global_conf()
        llm_config = {"config_list": config.get_llm_config_list()}

        # Create planner agent
        planner = BaseAgent.create_agent(
            agent_type="planner",
            name="planner",
            llm_config=llm_config,
            max_consecutive_auto_reply=self.planner_max_chat_round,
            system_message=LLM_PROMPTS["planner_agent_system_message"],
        )
        agents_map["planner"] = cast(ConversableAgent, planner)

        # Create navigator agent
        navigator = BaseAgent.create_agent(
            agent_type="navigator",
            name="navigator",
            llm_config=llm_config,
            max_consecutive_auto_reply=self.nav_max_chat_round,
            system_message=LLM_PROMPTS["navigation_agent_system_message"],
        )
        agents_map["navigator"] = cast(ConversableAgent, navigator)

        return agents_map

    def get_current_url(self) -> str:
        """
        Get the current URL from the device.

        Returns:
            The current URL as a string
        """
        device = self.device_manager.get_device_instance()
        return device.get_current_url()

    def get_current_device_view(self) -> str:
        """Get the current device view."""
        return "Current Device View: " + self.get_current_url()

    def get_next_step(self, next_step: str) -> str:
        """Get the next step with memory context."""
        mem_fetch = self._query_memory(next_step)
        return f"{next_step}\nRelevant context from memory: {mem_fetch}"

    def clean_up_plan(self) -> None:
        """Clean up after plan execution."""
        if not self.dont_terminate_device_after_run:
            self.device_manager.close()

    def _query_memory(self, context: str) -> str:
        """Query the memory system."""
        return self.memory.query(context)

    def process_command(self, command: str) -> Tuple[Any, float]:
        """
        Process a command using the agent system.

        Args:
            command: The command to process

        Returns:
            Tuple containing the result and execution time
        """
        start_time = time.time()

        try:
            # Query memory for relevant context
            mem_fetch = self._query_memory(command)
            prompt = f"{command}\nRelevant context from memory: {mem_fetch}"

            # Execute the command through the agent system
            planner = cast(ConversableAgent, self.agents_map["planner"])
            result = planner.initiate_chat(
                message=prompt,
            )

            # Calculate execution time
            end_time = time.time()
            execution_time = end_time - start_time

            return result, execution_time

        except Exception as e:
            logger.error(f"Error processing command: {e}")
            end_time = time.time()
            execution_time = end_time - start_time
            return {"error": str(e)}, execution_time

    def run(self, task: str) -> None:
        """
        Run a task using the SimpleHercules system.

        Args:
            task: The task description to execute
        """
        try:
            logger.info(f"Running task: {task}")
            planner = cast(ConversableAgent, self.agents_map["planner"])
            navigator = cast(ConversableAgent, self.agents_map["navigator"])

            # Initialize the chat between planner and navigator
            planner.initiate_chat(
                navigator,
                message=task,
            )

        except Exception as e:
            logger.error(f"Error running task: {str(e)}")
            raise
        finally:
            if not self.dont_terminate_device_after_run:
                self.device_manager.close()

    def close(self) -> None:
        """Close all resources and cleanup."""
        try:
            self.device_manager.close()
        except Exception as e:
            logger.error(f"Error closing device manager: {str(e)}")


def main() -> None:
    """Main entry point for SimpleHercules."""
    if len(sys.argv) < 2:
        print("Usage: python -m testzeus_hercules.core.simple_hercules <task>")
        sys.exit(1)

    task = sys.argv[1]
    hercules = SimpleHercules.create()

    try:
        hercules.run(task)
    finally:
        hercules.close()


if __name__ == "__main__":
    main()
