import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, cast

import aiofiles
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents_llm_config_manager import AgentsLLMConfigManager
from testzeus_hercules.core.playwright_manager import PlaywrightManager
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils.cli_helper import async_input  # type: ignore
from testzeus_hercules.utils.logger import logger


class BaseRunner:
    """
    Base class for runners that handle input processing for the system.
    """

    def __init__(
        self,
        planner_max_chat_round: int = 500,
        browser_nav_max_chat_round: int = 50,
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

        self.planner_agent_config: Dict[str, Any] | None = None
        self.nav_agent_config: Dict[str, Any] | None = None
        self.helper_config: Dict[str, Any] | None = None

    async def initialize(self) -> None:
        if not self.stake_id:
            raise ValueError("stake_id is required")

        config_manager = AgentsLLMConfigManager.get_instance()
        config_manager.initialize()

        planner_config = config_manager.get_agent_config("planner_agent")
        nav_config = config_manager.get_agent_config("nav_agent")
        helper_config = config_manager.get_agent_config("helper_agent")
        if not all([planner_config, nav_config, helper_config]):
            raise ValueError("Failed to get required agent configurations")

        self.planner_agent_config = dict(planner_config)
        self.nav_agent_config = dict(nav_config)
        self.helper_config = dict(helper_config)

        self.simple_hercules = await SimpleHercules.create(
            self.stake_id,
            self.planner_agent_config,
            self.nav_agent_config,
            self.helper_config,
            save_chat_logs_to_files=self.save_chat_logs_to_files,
            planner_max_chat_round=self.planner_number_of_rounds,
            browser_nav_max_chat_round=self.nav_agent_number_of_rounds,
        )

        self.browser_manager = PlaywrightManager(gui_input_mode=False, stake_id=self.stake_id)
        await self.browser_manager.async_initialize()

    async def clean_up(self) -> None:
        if self.simple_hercules:
            await self.simple_hercules.shutdown()
            self.simple_hercules = None
        if self.browser_manager:
            await self.browser_manager.stop_playwright()

    async def save_chat_logs(self) -> None:
        """Save planner chat logs to file or logger."""
        agents_map = cast(Dict[str, Any], self.simple_hercules.agents_map) if self.simple_hercules else {}
        res_output_thoughts_logs_di: Dict[str, List[Dict[str, Any]]] = {}

        if self.simple_hercules and self.simple_hercules._last_graph_result:
            res_output_thoughts_logs_di[self.planner_agent_name] = list(self.simple_hercules._last_graph_result.chat_history)
        elif self.planner_agent_name in agents_map:
            planner = agents_map[self.planner_agent_name]
            if hasattr(planner, "chat_messages"):
                for key, value in planner.chat_messages.items():
                    str_key = str(key)
                    if str_key in res_output_thoughts_logs_di:
                        res_output_thoughts_logs_di[str_key].extend(value)
                    else:
                        res_output_thoughts_logs_di[str_key] = value.copy()
        else:
            return

        for key, vals in res_output_thoughts_logs_di.items():
            for idx, val in enumerate(vals):
                logger.debug(f"Planner chat log: {val}")
                content = val["content"].replace("```json", "").replace("```", "").strip()
                try:
                    res_output_thoughts_logs_di[key][idx]["content"] = json.loads(content)
                except json.JSONDecodeError:
                    logger.debug(f"Failed to decode JSON: {content}, keeping as multiline string")
                    res_output_thoughts_logs_di[key][idx]["content"] = content

        if self.save_chat_logs_to_files:
            log_path = os.path.join(
                get_global_conf().get_source_log_folder_path(self.stake_id),
                "agent_inner_thoughts.json",
            )
            async with aiofiles.open(log_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(res_output_thoughts_logs_di, ensure_ascii=False, indent=4))
            logger.debug("Chat messages saved")
        else:
            logger.info("Planner chat log: ", extra={"planner_chat_log": res_output_thoughts_logs_di})

    async def process_command(self, command: str) -> tuple[Any, float]:
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

            if self.simple_hercules:
                await self.browser_manager.update_processing_state("processing")  # type: ignore
                result = await self.simple_hercules.process_command(command, current_url)
                await self.browser_manager.update_processing_state("done")  # type: ignore

            elapsed_time = round(time.time() - start_time, 2)

            await self.save_chat_logs()
            if result is not None:
                logger.info(f'Command "{command}" took: {elapsed_time} seconds.')
                chat_history = getattr(result, "chat_history", [])
                last_message = chat_history[-1] if chat_history else None
                if last_message and last_message.get("terminate") == "yes":
                    logger.info(f"Final message: {last_message}")

            await self.browser_manager.command_completed(command, elapsed_time)  # type: ignore
            self.is_running = False

        return result, elapsed_time

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        if self.simple_hercules:
            await self.simple_hercules.shutdown()
            self.simple_hercules = None
        if self.browser_manager:
            await self.browser_manager.stop_playwright()
        PlaywrightManager.close_all_instances()
        self.shutdown_event.set()

    async def start(self) -> None:
        raise NotImplementedError("Subclasses should implement this method.")


class CommandPromptRunner(BaseRunner):
    async def start(self) -> None:
        await self.initialize()
        command: str = await async_input("Enter your command (or type 'exit' to quit): ")
        await self.process_command(command)
        await self.clean_up()
        await self.shutdown_event.wait()


class SingleCommandInputRunner(BaseRunner):
    def __init__(self, command: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.command = command
        self.result = None
        self.execution_time: float = 0

    async def start(self) -> None:
        await self.initialize()
        self.result, self.execution_time = await self.process_command(self.command)
        if not self.dont_terminate_browser_after_run:
            await self.process_command("exit")
            await self.shutdown_event.wait()
