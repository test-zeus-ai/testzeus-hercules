import asyncio
import json
import os
import time
from typing import Any

import aiofiles
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents_llm_config import AgentsLLMConfig
from testzeus_hercules.core.device_manager import DeviceManager
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils.cli_helper import async_input  # type: ignore
from testzeus_hercules.utils.logger import logger


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
        planner_max_chat_round: int = 50,
        nav_max_chat_round: int = 5,
        stake_id: str | None = None,
        dont_terminate_browser_after_run: bool = False,
    ):
        self.planner_number_of_rounds = planner_max_chat_round
        self.nav_agent_number_of_rounds = nav_max_chat_round
        self.device_manager = None
        self.simple_hercules = None
        self.is_running = False
        self.stake_id = stake_id
        self.dont_terminate_browser_after_run = dont_terminate_browser_after_run

        self.save_chat_logs_to_files = os.getenv("SAVE_CHAT_LOGS_TO_FILE", "False").lower() in ["true", "1"]

        self.planner_agent_name = "planner_agent"
        self.shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """
        Initializes components for the system, including the Autogen wrapper and the Playwright manager.
        """
        llm_config = AgentsLLMConfig()
        self.planner_agent_config = llm_config.get_planner_agent_config()
        self.nav_agent_config = llm_config.get_nav_agent_config()
        self.mem_agent_config = llm_config.get_mem_agent_config()
        self.helper_config = llm_config.get_helper_agent_config()

        self.simple_hercules = await SimpleHercules.create(
            self.stake_id or "default",
            self.planner_agent_config,
            self.nav_agent_config,
            self.mem_agent_config,
            self.helper_config,
            save_chat_logs_to_files=self.save_chat_logs_to_files,
            planner_max_chat_round=self.planner_number_of_rounds,
            nav_max_chat_round=self.nav_agent_number_of_rounds,
        )

        self.device_manager = DeviceManager(stake_id=self.stake_id).get_device_instance()
        await self.device_manager.async_initialize()

    async def clean_up_plan(self) -> None:
        """
        Clean up the plan after each command is processed.
        """
        if self.simple_hercules:
            await self.simple_hercules.clean_up_plan()

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
            
            device_start = time.time()
            current_url = await self.device_manager.get_current_screen_state() if self.device_manager else None
            device_time = time.time() - device_start
            
            result = None
            llm_time = 0
            logger.info(f"Processing command: {command}")
            if self.simple_hercules:
                await self.device_manager.update_processing_state("processing")  # type: ignore
                
                llm_start = time.time()
                result = await self.simple_hercules.process_command(command, current_url)
                llm_time = time.time() - llm_start
                
                await self.device_manager.update_processing_state("done")  # type: ignore
            
            io_start = time.time()
            await self.save_planner_chat_messages()
            io_time = time.time() - io_start
            
            end_time = time.time()
            elapsed_time = round(end_time - start_time, 2)
            
            logger.info(f'Command "{command}" took: {elapsed_time} seconds.')
            logger.info(f'Performance breakdown - Device: {device_time:.2f}s, LLM: {llm_time:.2f}s, I/O: {io_time:.2f}s')
            
            if result is not None:
                chat_history = result.chat_history  # type: ignore
                last_message = chat_history[-1] if chat_history else None  # type: ignore
                if last_message and "terminate" in last_message and last_message["terminate"] == "yes":
                    logger.info(f"Final message: {last_message}")

            await self.device_manager.command_completed(command, elapsed_time)  # type: ignore
            self.is_running = False
        return result, elapsed_time

    async def save_planner_chat_messages(self) -> None:
        """
        Saves chat messages to a file or logs them based on configuration.
        """
        if not self.simple_hercules or not self.simple_hercules.agents_map:
            return
            
        messages = self.simple_hercules.agents_map[self.planner_agent_name].chat_messages
        messages_str_keys = {str(key): value for key, value in messages.items()}
        res_output_thoughts_logs_di = {}
        for key, value in messages_str_keys.items():
            if res_output_thoughts_logs_di.get(self.planner_agent_name):
                res_output_thoughts_logs_di[self.planner_agent_name] += value
            else:
                res_output_thoughts_logs_di[self.planner_agent_name] = value

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

    async def shutdown(self) -> None:
        """
        Shuts down the components gracefully.
        """
        logger.info("Shutting down...")
        if self.device_manager and self.stake_id:
            await self.device_manager.close_instance(self.stake_id)
        await DeviceManager.close_all_instances()
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
            command: str = await async_input("Enter your command (or type 'exit' to quit): ")
            await self.process_command(command)
            await self.clean_up_plan()
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
        self.execution_time = 0

    async def start(self) -> None:
        """
        Processes commands from a file.
        """
        await self.initialize()
        self.result, self.execution_time = await self.process_command(self.command)
        if not self.dont_terminate_browser_after_run:
            _ = await self.process_command("exit")
            await self.wait_for_exit()
