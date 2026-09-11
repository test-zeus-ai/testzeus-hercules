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
from testzeus_hercules.utils.cleanup_helper import raise_if_cancelled
from testzeus_hercules.utils.cli_helper import async_input  # type: ignore
from testzeus_hercules.utils.env_flags import should_skip_browser
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

        if should_skip_browser():
            logger.info("SKIP_BROWSER is set, running without a browser")
            return

        self.browser_manager = PlaywrightManager(gui_input_mode=False, stake_id=self.stake_id)
        await self.browser_manager.async_initialize()

    async def clean_up(self) -> None:
        # Collect every cleanup failure: agent shutdown and browser stop both
        # run, and the combined errors surface at the end (cancellation keeps
        # precedence) instead of first-error-wins hiding the rest.
        # CancelledError is collected per step too, so a cancellation during
        # the agent shutdown cannot skip the browser stop; raise_if_cancelled
        # restores cancellation precedence once everything has been torn down.
        errors: list[BaseException] = []
        if self.simple_hercules:
            try:
                await self.simple_hercules.shutdown()
            except (Exception, asyncio.CancelledError) as exc:
                logger.error(f"Error during simple_hercules shutdown: {exc!r}")
                errors.append(exc)
            self.simple_hercules = None
        if self.browser_manager:
            try:
                await self.browser_manager.stop_playwright()
            except (Exception, asyncio.CancelledError) as exc:
                logger.error(f"Error stopping playwright: {exc!r}")
                errors.append(exc)
            self.browser_manager = None
        raise_if_cancelled(errors)
        if errors:
            raise BaseExceptionGroup("Harness cleanup failures", errors)

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
            logger.info(
                "Planner chat log: ",
                extra={"planner_chat_log": res_output_thoughts_logs_di},
            )

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
                if self.browser_manager:
                    await self.browser_manager.update_processing_state("processing")
                result = await self.simple_hercules.process_command(command, current_url)
                if self.browser_manager:
                    await self.browser_manager.update_processing_state("done")

            elapsed_time = round(time.time() - start_time, 2)

            await self.save_chat_logs()
            if result is not None:
                logger.info(f'Command "{command}" took: {elapsed_time} seconds.')
                chat_history = getattr(result, "chat_history", [])
                last_message = chat_history[-1] if chat_history else None
                if last_message and last_message.get("terminate") == "yes":
                    logger.info(f"Final message: {last_message}")

            if self.browser_manager:
                await self.browser_manager.command_completed(command, elapsed_time)
            self.is_running = False

        return result, elapsed_time

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        if self.simple_hercules:
            await self.simple_hercules.shutdown()
            self.simple_hercules = None
        if self.browser_manager:
            await self.browser_manager.stop_playwright()
        # Await the teardown of every registered instance (including this
        # runner's) so leftover browsers from any stake are reclaimed here.
        await PlaywrightManager.close_all_instances_async()
        self._teardown_appium_instances()
        self.shutdown_event.set()

    @staticmethod
    def _teardown_appium_instances() -> None:
        """Best-effort Appium teardown so an aborted scenario cannot leak the
        device session, its recording, or the manager's worker pool.

        Mobile support is optional: when the AppiumManager is unavailable
        this is a silent no-op. Failures are logged, never raised, and this
        runs after the Playwright teardown so it can never block or mask it.
        """
        try:
            from testzeus_hercules.core.mobile.appium_manager import AppiumManager
        except Exception as e:
            logger.debug("Appium cleanup skipped (mobile automation unavailable): %s", e)
            return
        try:
            AppiumManager.close_all_instances()
            logger.debug("Appium instances closed during shutdown")
        except Exception as e:
            logger.debug("Error during Appium cleanup at shutdown: %s", e)

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
        self._start_failed = False

    async def start(self) -> None:
        try:
            await self.initialize()
            self.result, self.execution_time = await self.process_command(self.command)
        except Exception:
            # A crashing command must never leak the browser/agents in a
            # long-lived process. Cleanup here is best-effort: its errors are
            # logged, never allowed to mask the original failure, which is
            # re-raised so the caller records the failed scenario.
            self._start_failed = True
            try:
                await self.clean_up()
            except BaseException as cleanup_exc:  # noqa: BLE001 - best-effort teardown
                logger.error(f"Clean up after failed command failed: {cleanup_exc}")
            raise
        finally:
            # A failed scenario leaves the environment untrusted, so the browser
            # is closed even when dont_terminate_browser_after_run is set.
            if not self.dont_terminate_browser_after_run or self._start_failed:
                await self._terminate()

    async def _terminate(self) -> None:
        """Run the normal exit path and guarantee teardown even when it fails.

        Ensures ``shutdown_event`` is always set (so ``start`` cannot hang) and
        the browser/agents are stopped even when ``process_command("exit")``
        itself raises. ``BaseExceptionGroup`` is caught alongside ``Exception``
        because the aggregated cleanup helpers raise it.
        """
        try:
            await self.process_command("exit")
        except (Exception, BaseExceptionGroup) as exc:
            logger.error(f"Exit command failed during teardown: {exc}")
        if not self.shutdown_event.is_set():
            try:
                await self.clean_up()
            except (Exception, BaseExceptionGroup) as exc:
                logger.error(f"Clean up during teardown failed: {exc}")
            try:
                await self.shutdown()
            except (Exception, BaseExceptionGroup) as exc:
                logger.error(f"Shutdown during teardown failed: {exc}")
            if not self.shutdown_event.is_set():
                self.shutdown_event.set()
        await self.shutdown_event.wait()
