import asyncio
import json
import os
import time
from typing import Any

from testzeus_hercules.config import get_source_log_folder_path
from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.core.autogen_simple_wrapper import AutogenSimpleWrapper
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.utils.cli_helper import async_input  # type: ignore
from testzeus_hercules.utils.logger import logger


class BaseRunner:
    """
    A base class for runners that handle input processing mechanisms for the system.

    Attributes:
        planner_number_of_rounds (int): The maximum number of chat rounds for the planner.
        browser_number_of_rounds (int): The maximum number of chat rounds for the browser navigation agent.
        save_chat_logs_to_files (bool): Flag indicating whether to save chat logs to files.
    """

    def __init__(
        self,
        planner_max_chat_round: int = 50,
        browser_nav_max_chat_round: int = 10,
        stake_id: str | None = None,
    ):
        self.planner_number_of_rounds = planner_max_chat_round
        self.browser_number_of_rounds = browser_nav_max_chat_round
        self.browser_manager = None
        self.autogen_wrapper = None
        self.is_running = False
        self.stake_id = stake_id

        self.save_chat_logs_to_files = os.getenv(
            "SAVE_CHAT_LOGS_TO_FILE", "True"
        ).lower() in ["true", "1"]

        self.planner_agent_name = "planner_agent"
        self.shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """
        Initializes components for the system, including the Autogen wrapper and the Playwright manager.
        """
        llm_config = AgentsLLMConfig()
        self.planner_agent_config = llm_config.get_planner_agent_config()
        self.browser_nav_agent_config = llm_config.get_browser_nav_agent_config()

        self.autogen_wrapper = await AutogenSimpleWrapper.create(
            self.planner_agent_config,
            self.browser_nav_agent_config,
            save_chat_logs_to_files=self.save_chat_logs_to_files,
            planner_max_chat_round=self.planner_number_of_rounds,
            browser_nav_max_chat_round=self.browser_number_of_rounds,
        )

        self.browser_manager = PlaywrightManager(
            gui_input_mode=False, stake_id=self.stake_id
        )
        await self.browser_manager.async_initialize()

    async def process_command(self, command: str) -> tuple[Any, float]:
        """
        Processes a command, interacting with the Autogen wrapper and Playwright manager.

        Args:
            command (str): The command to process.

        Returns:
            Any: The result of processing the command, if any.
            int: The elapsed time for processing the command.
        """
        result = None
        elapsed_time = 0
        logger.info(f"Received command: {command}")
        if command.lower() == "exit":
            await self.shutdown()
            return result, elapsed_time

        if command:
            self.is_running = True
            start_time = time.time()
            current_url = (
                await self.browser_manager.get_current_url()
                if self.browser_manager
                else None
            )
            self.browser_manager.log_user_message(command)  # type: ignore
            result = None
            logger.info(f"Processing command: {command}")
            if self.autogen_wrapper:
                await self.browser_manager.update_processing_state("processing")  # type: ignore
                result = await self.autogen_wrapper.process_command(
                    command, current_url
                )
                await self.browser_manager.update_processing_state("done")  # type: ignore
            end_time = time.time()
            elapsed_time = round(end_time - start_time, 2)
            logger.info(f'Command "{command}" took: {elapsed_time} seconds. and total cost metric is {result.cost}')  # type: ignore
            await self.save_planner_chat_messages()
            if result is not None:
                chat_history = result.chat_history  # type: ignore
                last_message = chat_history[-1] if chat_history else None  # type: ignore
                if (
                    last_message
                    and "terminate" in last_message
                    and last_message["terminate"] == "yes"
                ):
                    await self.browser_manager.notify_user(last_message, "answer")  # type: ignore

            await self.browser_manager.notify_user(f"Task Completed ({elapsed_time}s).", "info")  # type: ignore
            await self.browser_manager.command_completed(command, elapsed_time)  # type: ignore
            self.is_running = False
        return result, elapsed_time

    async def save_planner_chat_messages(self) -> None:
        """
        Saves chat messages to a file or logs them based on configuration.
        """
        messages = self.autogen_wrapper.agents_map[
            self.planner_agent_name
        ].chat_messages
        messages_str_keys = {str(key): value for key, value in messages.items()}

        if self.save_chat_logs_to_files:
            with open(
                os.path.join(
                    get_source_log_folder_path(self.stake_id), "chat_messages.json"
                ),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(messages_str_keys, f, ensure_ascii=False, indent=4)
            logger.debug("Chat messages saved")
        else:
            logger.info(
                "Planner chat log: ", extra={"planner_chat_log": messages_str_keys}
            )

    async def shutdown(self) -> None:
        """
        Shuts down the components gracefully.
        """
        logger.info("Shutting down...")
        if self.browser_manager:
            await self.browser_manager.stop_playwright()
        self.shutdown_event.set()

    async def wait_for_exit(self) -> None:
        """
        Waits for an exit command to be processed, keeping the system active in the meantime.
        """
        await self.shutdown_event.wait()  # Wait until the shutdown event is set

    async def start(self) -> None:
        """
        Starts the input processing mechanism.
        """
        raise NotImplementedError("Subclasses should implement this method.")


class CommandPromptRunner(BaseRunner):
    """
    A runner that handles input from the command prompt in a loop.
    """

    async def start(self) -> None:
        """
        Starts the command prompt input loop.
        """
        await self.initialize()
        while not self.is_running:
            command: str = await async_input(
                "Enter your command (or type 'exit' to quit): "
            )
            await self.process_command(command)
            if self.shutdown_event.is_set():
                break
        await self.wait_for_exit()


class SingleCommandInputRunner(BaseRunner):
    """
    A runner that handles input command and return the result.
    """

    def __init__(self, command: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.command = command
        self.result = None
        self.execution_time = 0

    async def start(self) -> None:
        """
        Processes commands from a file.
        """
        await self.initialize()
        self.result, self.execution_time = await self.process_command(self.command)
        _ = await self.process_command("exit")
        await self.wait_for_exit()
