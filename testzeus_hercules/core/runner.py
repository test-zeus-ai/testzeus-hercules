import time
from typing import Any, Optional, Tuple, Union

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.device_manager import DeviceManager
from testzeus_hercules.core.simple_hercules import SimpleHercules
from testzeus_hercules.utils.logger import logger


class BaseRunner:
    """
    Base class for runners that process commands.
    """

    def __init__(
        self,
        stake_id: Optional[str] = None,
        planner_number_of_rounds: int = 500,
        nav_agent_number_of_rounds: int = 10,
        save_chat_logs_to_files: bool = False,
        dont_terminate_browser_after_run: bool = False,
    ):
        self.stake_id = stake_id
        self.planner_number_of_rounds = planner_number_of_rounds
        self.nav_agent_number_of_rounds = nav_agent_number_of_rounds
        self.save_chat_logs_to_files = save_chat_logs_to_files
        self.dont_terminate_browser_after_run = dont_terminate_browser_after_run
        self.simple_hercules = None
        self.device_manager = None
        self.is_running = False
        self.shutdown_event = False

    def initialize(self) -> None:
        """
        Initializes components for the system, including the SimpleHercules and device manager.
        """
        self.simple_hercules = SimpleHercules.create(
            planner_max_chat_round=self.planner_number_of_rounds,
            nav_max_chat_round=self.nav_agent_number_of_rounds,
            dont_terminate_browser_after_run=self.dont_terminate_browser_after_run,
        )

        self.device_manager = DeviceManager(
            stake_id=self.stake_id
        ).get_device_instance()
        self.device_manager.initialize()

    def clean_up_plan(self) -> None:
        """
        Clean up the plan after each command is processed.
        """
        if self.simple_hercules:
            self.simple_hercules.clean_up_plan()

    def process_command(self, command: str) -> Tuple[Any, float]:
        """
        Processes a command, interacting with SimpleHercules and device manager.

        Args:
            command (str): The command to process.

        Returns:
            Any: The result of processing the command, if any.
            float: The elapsed time for processing the command.
        """
        result = None
        elapsed_time = 0
        logger.info(f"Received command: {command}")

        if command.lower() == "exit":
            self.shutdown()
            return result, elapsed_time

        if command:
            self.is_running = True
            start_time = time.time()

            # Get current screen state if device manager exists
            current_url = (
                self.device_manager.get_current_screen_state()
                if self.device_manager
                else None
            )

            result = None
            logger.info(f"Processing command: {command}")

            if self.simple_hercules:
                if self.device_manager:
                    self.device_manager.update_processing_state("processing")
                result = self.simple_hercules.process_command(command)
                if self.device_manager:
                    self.device_manager.update_processing_state("done")

            end_time = time.time()
            elapsed_time = round(end_time - start_time, 2)

            if result is not None:
                logger.info(f'Command "{command}" took: {elapsed_time} seconds.')

                # Check if we should terminate
                chat_history = getattr(result, "chat_history", [])
                last_message = chat_history[-1] if chat_history else None
                if last_message and last_message.get("terminate") == "yes":
                    logger.info(f"Final message: {last_message}")

            if self.device_manager:
                self.device_manager.command_completed(command, elapsed_time)

            self.is_running = False

        return result, elapsed_time

    def shutdown(self) -> None:
        """
        Shuts down the components gracefully.
        """
        logger.info("Shutting down...")
        if self.device_manager and self.stake_id:
            self.device_manager.close_instance(self.stake_id)
        DeviceManager.close_all_instances()
        self.shutdown_event = True

    def start(self) -> None:
        """
        Starts the input processing mechanism.
        """
        raise NotImplementedError("Subclasses should implement this method.")


class CommandPromptRunner(BaseRunner):
    """
    A runner that handles input from the command prompt in a loop.
    """

    def start(self) -> None:
        """
        Starts the command prompt input loop.
        """
        self.initialize()
        while not self.is_running:
            command = input("Enter your command (or type 'exit' to quit): ")
            self.process_command(command)
            self.clean_up_plan()
            if self.shutdown_event:
                break


class SingleCommandInputRunner(BaseRunner):
    """
    A runner that handles a single input command and returns the result.
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

    def start(self) -> None:
        """
        Process the single command.
        """
        self.initialize()
        self.result, self.execution_time = self.process_command(self.command)
        if not self.dont_terminate_browser_after_run:
            _ = self.process_command("exit")
