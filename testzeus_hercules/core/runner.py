import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, cast

import aiofiles
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils.cli_helper import async_input  # type: ignore
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.core.playwright_manager import generate_playwright_test_file

class BaseRunner:
    """
    A base class for runners that handle input processing mechanisms for the system.

    Attributes:
        planner_number_of_rounds (int): The maximum number of chat rounds for the planner.
        nav_agent_number_of_rounds (int): The maximum number of chat rounds for the browser navigation agent.
        save_chat_logs_to_files (bool): Flag indicating whether to save chat logs to files.
    """

    def __init__(
        self,
        planner_max_chat_round: int = 500,
        browser_nav_max_chat_round: int = 10,
        stake_id: str | None = None,
        dont_terminate_browser_after_run: bool = False,
    ):
        self.planner_number_of_rounds = planner_max_chat_round
        self.nav_agent_number_of_rounds = browser_nav_max_chat_round
        self.browser_manager: PlaywrightManager | None = None
        self.simple_hercules: SimpleHercules | None = None
        self.is_running = False
        self.stake_id = stake_id
        self.dont_terminate_browser_after_run = dont_terminate_browser_after_run

        self.save_chat_logs_to_files = os.getenv("SAVE_CHAT_LOGS_TO_FILE", "True").lower() in ["true", "1"]

        self.planner_agent_name = "planner_agent"
        self.shutdown_event = asyncio.Event()

        # Agent configurations
        self.planner_agent_config: Dict[str, Any] | None = None
        self.nav_agent_config: Dict[str, Any] | None = None
        self.mem_agent_config: Dict[str, Any] | None = None
        self.helper_config: Dict[str, Any] | None = None

    async def initialize(self) -> None:
        """
        Initializes components for the system, including the Autogen wrapper and the Playwright manager.
        """
        if not self.stake_id:
            raise ValueError("stake_id is required")

        config_manager = AgentsLLMConfigManager.get_instance()
        config_manager.initialize()

        # Get configurations and convert to Dict[str, Any]
        planner_config = config_manager.get_agent_config("planner_agent")
        nav_config = config_manager.get_agent_config("nav_agent")
        mem_config = config_manager.get_agent_config("mem_agent")
        helper_config = config_manager.get_agent_config("helper_agent")

        if not all([planner_config, nav_config, mem_config, helper_config]):
            raise ValueError("Failed to get required agent configurations")

        # Convert TypedDict to Dict[str, Any]
        self.planner_agent_config = dict(planner_config)
        self.nav_agent_config = dict(nav_config)
        self.mem_agent_config = dict(mem_config)
        self.helper_config = dict(helper_config)

        self.simple_hercules = await SimpleHercules.create(
            self.stake_id,
            self.planner_agent_config,
            self.nav_agent_config,
            self.mem_agent_config,
            self.helper_config,
            save_chat_logs_to_files=self.save_chat_logs_to_files,
            planner_max_chat_round=self.planner_number_of_rounds,
            browser_nav_max_chat_round=self.nav_agent_number_of_rounds,
        )

        self.browser_manager = PlaywrightManager(gui_input_mode=False, stake_id=self.stake_id)
        await self.browser_manager.async_initialize()

    async def clean_up(self) -> None:
        """Clean up resources."""
        if self.simple_hercules:
            await self.simple_hercules.clean_up_plan()
        if self.browser_manager:
            await self.browser_manager.stop_playwright()

    def get_agents_map(self) -> Dict[str, Any]:
        """Get the agents map from SimpleHercules."""
        if not self.simple_hercules or not self.simple_hercules.agents_map:
            return {}
        agents_map = cast(Dict[str, Any], self.simple_hercules.agents_map)
        return agents_map

    async def save_chat_logs(self, agents_map: Dict[str, Any]) -> None:
        """Save chat logs to files."""
        if not self.planner_agent_name in agents_map:
            return

        messages = agents_map[self.planner_agent_name].chat_messages
        messages_str_keys: Dict[str, List[Dict[str, Any]]] = {str(key): value for key, value in messages.items()}
        res_output_thoughts_logs_di: Dict[str, List[Dict[str, Any]]] = {}

        for key, value in messages_str_keys.items():
            if self.planner_agent_name in res_output_thoughts_logs_di:
                res_output_thoughts_logs_di[self.planner_agent_name].extend(value)
            else:
                res_output_thoughts_logs_di[self.planner_agent_name] = value.copy()

        for key, vals in res_output_thoughts_logs_di.items():
            # logger.debug(f"Planner chat log: {key} : {vals}")
            for idx, val in enumerate(vals):
                logger.debug(f"Planner chat log: {val}")
                content = val["content"]
                content = content.replace("```json", "").replace("```", "").strip()
                res_content = None
                try:
                    res_content = json.loads(content)
                except json.JSONDecodeError:
                    logger.debug(f"Failed to decode JSON: {content}, keeping as multiline string")
                    res_content = content
                res_output_thoughts_logs_di[key][idx]["content"] = res_content

        if self.save_chat_logs_to_files:
            async with aiofiles.open(
                os.path.join(
                    get_global_conf().get_source_log_folder_path(self.stake_id),
                    "agent_inner_thoughts.json",
                ),
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(json.dumps(res_output_thoughts_logs_di, ensure_ascii=False, indent=4))
            logger.debug("Chat messages saved")
        else:
            logger.info(
                "Planner chat log: ",
                extra={"planner_chat_log": res_output_thoughts_logs_di},
            )

    async def process_command(self, command: str) -> tuple[Any, float]:
        """
        Processes a command, interacting with the Autogen wrapper and Playwright manager.

        Args:
            command (str): The command to process.

        Returns:
            Any: The result of processing the command, if any.
            float: The elapsed time for processing the command.
        """
        result = None
        elapsed_time: float = 0.0
        logger.info(f"Received command: {command}")
        if command.lower() == "exit":
            await self.shutdown()
            return result, elapsed_time

        if command:
            self.is_running = True
            start_time = time.time()
            current_url = await self.browser_manager.get_current_url() if self.browser_manager else None
            result = None
            logger.info(f"Processing command: {command}")
            if self.simple_hercules:
                await self.browser_manager.update_processing_state("processing")  # type: ignore
                result = await self.simple_hercules.process_command(command, current_url)
                await self.browser_manager.update_processing_state("done")  # type: ignore
            end_time = time.time()
            elapsed_time = round(end_time - start_time, 2)

            await self.save_planner_chat_messages()
            if result is not None:
                logger.info(f'Command "{command}" took: {elapsed_time} seconds.')
                # Get trace paths from config
                chat_history = result.chat_history  # type: ignore
                last_message = chat_history[-1] if chat_history else None  # type: ignore
                if last_message and "terminate" in last_message and last_message["terminate"] == "yes":
                    logger.info(f"Final message: {last_message}")

            await self.browser_manager.command_completed(command, elapsed_time)  # type: ignore
            self.is_running = False
        return result, elapsed_time

    async def save_planner_chat_messages(self) -> None:
        """
        Saves chat messages to a file or logs them based on configuration.
        """
        await self.save_chat_logs(self.get_agents_map())

    async def shutdown(self) -> None:
        """
        Shuts down the components gracefully.
        """
        logger.info("Shutting down...")
        if self.browser_manager:
            await self.browser_manager.stop_playwright()
        PlaywrightManager.close_all_instances()
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

    async def run_planner_agent(self) -> None:
        """Run the planner agent."""
        start_time = time.time()
        # ... existing code ...
        elapsed_time = time.time() - start_time
        self.planner_number_of_rounds = max(1, round(elapsed_time))
        # ... existing code ...

    async def run_browser_nav_agent(self) -> None:
        """Run the browser navigation agent."""
        start_time = time.time()
        # ... existing code ...
        elapsed_time = time.time() - start_time
        self.nav_agent_number_of_rounds = max(1, round(elapsed_time))
        # ... existing code ...


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
            command: str = await async_input("Enter your command (or type 'exit' to quit): ")
            await self.process_command(command)
            await self.clean_up()
            if self.shutdown_event.is_set():
                break
        await self.wait_for_exit()


class SingleCommandInputRunner(BaseRunner):
    """
    A runner that handles input command and return the result.
    """

    def __init__(
        self,
        command: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.command = command
        self.result = None
        self.execution_time: float = 0

    async def start(self) -> None:
        """
        Processes commands from a file.
        """
        await self.initialize()
        self.result, self.execution_time = await self.process_command(self.command)
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        _file_name = f"{get_global_conf().get_staked_id()}_{timestamp}.py"
        gen_code_dir = os.path.join(get_global_conf().get_current_script_dir(), _file_name)
        generate_playwright_test_file(gen_code_dir)
        if not self.dont_terminate_browser_after_run:
            _ = await self.process_command("exit")
            await self.wait_for_exit()
