import asyncio
import datetime
import json
import os
import tempfile
import traceback
from string import Template
from typing import Any

import autogen  # type: ignore
import nest_asyncio  # type: ignore
import openai
from autogen import Cache
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.agents.api_nav_agent import ApiNavAgent
from testzeus_hercules.core.agents.browser_nav_agent import BrowserNavAgent
from testzeus_hercules.core.agents.high_level_planner_agent import PlannerAgent
from testzeus_hercules.core.agents.sec_nav_agent import SecNavAgent
from testzeus_hercules.core.agents.sql_nav_agent import SqlNavAgent
from testzeus_hercules.core.agents.static_waiter_nav_agent import StaticWaiterNavAgent
from testzeus_hercules.core.extra_tools import *
from testzeus_hercules.core.memory.state_handler import store_run_data
from testzeus_hercules.core.post_process_responses import (
    final_reply_callback_planner_agent as notify_planner_messages,  # type: ignore
)
from testzeus_hercules.core.prompts import LLM_PROMPTS
from testzeus_hercules.core.tools import *
from testzeus_hercules.core.tools.get_url import geturl
from testzeus_hercules.telemetry import EventData, EventType, add_event
from testzeus_hercules.utils.detect_llm_loops import is_agent_stuck_in_loop
from testzeus_hercules.utils.llm_helper import (
    convert_model_config_to_autogen_format,
    extract_target_helper,
    format_plan_steps,
    parse_agent_response,
    process_chat_message_content,
)
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.utils.response_parser import parse_response
from testzeus_hercules.utils.sequential_function_call import (
    UserProxyAgent_SequentialFunctionExecution,
)
from testzeus_hercules.utils.timestamp_helper import get_timestamp_str
from testzeus_hercules.utils.ui_messagetype import MessageType

nest_asyncio.apply()  # type: ignore
from autogen import oai


class SimpleHercules:
    """
    A wrapper class for interacting with the Autogen library.

    Args:
        max_chat_round (int): The maximum number of chat rounds.

    Attributes:
        number_of_rounds (int): The maximum number of chat rounds.
        agents_map (dict): A dictionary of the agents that are instantiated in this autogen instance.

    """

    def __init__(
        self,
        stake_id: str,
        save_chat_logs_to_files: bool = True,
        planner_max_chat_round: int = 500,
        browser_nav_max_chat_round: int = 10,
    ):
        self.timestamp = get_timestamp_str()  # Add this line to store timestamp
        oai.Completion.set_cache(5, cache_path_root=".cache")
        self.planner_number_of_rounds = planner_max_chat_round
        self.nav_agent_number_of_rounds = browser_nav_max_chat_round

        self.agents_map: (
            dict[
                str,
                UserProxyAgent_SequentialFunctionExecution | autogen.AssistantAgent | autogen.ConversableAgent,
            ]
            | None
        ) = None

        self.planner_agent_model_config: list[dict[str, str]] | None = None
        self.browser_nav_agent_model_config: list[dict[str, str]] | None = None
        self.api_nav_agent_model_config: list[dict[str, str]] | None = None
        self.sec_nav_agent_model_config: list[dict[str, str]] | None = None
        self.sql_nav_agent_model_config: list[dict[str, str]] | None = None
        self.static_waiter_nav_agent_model_config: list[dict[str, str]] | None = None

        self.planner_agent_config: dict[str, Any] | None = None
        self.nav_agent_config: dict[str, Any] | None = None
        self.stake_id = stake_id
        self.chat_logs_dir: str = get_global_conf().get_source_log_folder_path(self.stake_id)
        self.save_chat_logs_to_files = save_chat_logs_to_files

    @classmethod
    async def create(
        cls,
        stake_id: str,
        planner_agent_config: dict[str, Any],
        nav_agent_config: dict[str, Any],
        save_chat_logs_to_files: bool = True,
        planner_max_chat_round: int = 500,
        browser_nav_max_chat_round: int = 10,
    ) -> "SimpleHercules":
        """
        Create an instance of SimpleHercules.

        Args:
            planner_agent_config: dict[str, Any]: A dictionary containing the configuration parameters for the planner agent. For example:
                {
                    "model_name": "gpt-4o",
                    "model_api_key": "",
                    "model_base_url": null,
                    "system_prompt": ["optional prompt unless you want to use the built in"],
                    "llm_config_params": { #all name value pairs here will go to the llm config of autogen verbatim
                        "cache_seed": null,
                        "temperature": 0.001,
                        "top_p": 0.001
                    }
                }
            nav_agent_config: dict[str, Any]: A dictionary containing the configuration parameters for the browser navigation agent. Same format as planner_agent_config.
            save_chat_logs_to_files (bool, optional): Whether to save chat logs to files. Defaults to True.
            planner_max_chat_rounds (int, optional): The maximum number of chat rounds for the planner. Defaults to 50.
            browser_nav_max_chat_round (int, optional): The maximum number of chat rounds for the browser navigation agent. Defaults to 10.

        Returns:
            SimpleHercules: An instance of SimpleHercules.

        """
        logger.info(
            f">>> Creating SimpleHercules, Planner max chat rounds: {planner_max_chat_round}, browser nav max chat rounds: {browser_nav_max_chat_round}. Save chat logs to files: {save_chat_logs_to_files}"
        )
        # Create an instance of cls
        self = cls(
            stake_id,
            save_chat_logs_to_files=save_chat_logs_to_files,
            planner_max_chat_round=planner_max_chat_round,
            browser_nav_max_chat_round=browser_nav_max_chat_round,
        )

        os.environ["AUTOGEN_USE_DOCKER"] = "False"

        self.planner_agent_config = planner_agent_config
        self.nav_agent_config = nav_agent_config

        self.planner_agent_model_config = convert_model_config_to_autogen_format(self.planner_agent_config["model_config_params"])
        self.browser_nav_agent_model_config = convert_model_config_to_autogen_format(self.nav_agent_config["model_config_params"])
        self.api_nav_agent_model_config = convert_model_config_to_autogen_format(self.nav_agent_config["model_config_params"])
        self.sec_nav_agent_model_config = convert_model_config_to_autogen_format(self.nav_agent_config["model_config_params"])
        self.sql_nav_agent_model_config = convert_model_config_to_autogen_format(self.nav_agent_config["model_config_params"])
        self.static_waiter_nav_agent_model_config = convert_model_config_to_autogen_format(self.nav_agent_config["model_config_params"])
        self.agents_map = await self.__initialize_agents()

        def trigger_nested_chat(manager: autogen.ConversableAgent) -> bool:  # type: ignore
            content: str = manager.last_message(manager.last_speaker)["content"] if isinstance(manager, autogen.GroupChatManager) else manager.last_message()["content"]

            parsed = parse_agent_response(content)
            next_step = parsed.get("next_step")
            plan = parsed.get("plan")

            if plan is not None and isinstance(plan, list):
                plan = format_plan_steps(plan)
                notify_planner_messages(plan, message_type=MessageType.PLAN)

            if next_step is None:
                notify_planner_messages("Received no response, terminating..", message_type=MessageType.INFO)
                return False
            notify_planner_messages(next_step, message_type=MessageType.STEP)
            return True

        def get_url() -> str:
            # return geturl()
            return asyncio.run(geturl())

        def my_custom_summary_method(sender: autogen.ConversableAgent, recipient: autogen.ConversableAgent, summary_args: dict):  # type: ignore
            # messages_str_keys = {str(key): value for key, value in sender.chat_messages.items()}  # type: ignore
            # self.__save_chat_log(list(messages_str_keys.values())[0])  # type: ignore
            self.__save_chat_log(sender, recipient)  # type: ignore
            do_we_need_get_url = False
            if isinstance(recipient, autogen.GroupChatManager):

                if "browser" in recipient.last_speaker.name:
                    do_we_need_get_url = True
                last_message = recipient.last_message(recipient.last_speaker)["content"]
            else:
                last_message = recipient.last_message(sender)["content"]  # type: ignore
            if not last_message or last_message.strip() == "":  # type: ignore
                # logger.info(f">>> Last message from browser nav was empty. Max turns: {self.nav_agent_number_of_rounds*2}, number of messages: {len(list(sender.chat_messages.items())[0][1])}")
                # logger.info(">>> Sender messages:", json.dumps( list(sender.chat_messages.items())[0][1], indent=2))
                return "I received an empty message. This is not an error and is recoverable. Try to reformulate the task..."
            elif "##TERMINATE TASK##" in last_message:
                last_message = last_message.replace("##TERMINATE TASK##", "")  # type: ignore
                if last_message and do_we_need_get_url:
                    last_message += " " + get_url()
                else:
                    mem = "Context from previous steps: " + last_message + "\n"
                    store_run_data(mem)
                # t_l = last_message.strip()
                # if not t_l:
                #     logger.info("Last message from browser nav was empty. Max turns: {self.nav_agent_number_of_rounds*2}, number of messages: {len(list(sender.chat_messages.items())[0][1])}")
                notify_planner_messages(last_message, message_type=MessageType.ACTION)  # type: ignore
                return last_message  #  type: ignore
            notify_planner_messages(last_message, message_type=MessageType.ACTION)  # type: ignore
            return last_message  # type: ignore

        def reflection_message(recipient, messages, sender, config):  # type: ignore
            last_message = messages[-1]["content"]  # type: ignore
            content_json = parse_response(last_message)  # type: ignore
            next_step = content_json.get("next_step", None)
            target_helper = content_json.get("target_helper", "Not_Applicable")
            if target_helper == "Not_Applicable":
                target_helper = ""

            if next_step is None:
                logger.error("Message to nested chat returned None")
                return None
            else:
                url = ""
                if "browser" in target_helper:
                    url = get_url()
                if target_helper.strip():
                    next_step = next_step.strip() + " " + url + f" ##target_helper: {target_helper}##"  # type: ignore
                    return next_step  # type: ignore
                else:
                    logger.error("Target helper not found in the response")
                    # this is some crazy trick, might backfire in long run, only time will tell.
                    return "skip this step and return only JSON"  # type: ignore

        # Updated logic to handle agent names with underscores
        nav_agents_names = list(
            set(
                [
                    "_".join(agent_name.split("_")[:-2])  # Take all parts except 'nav_agent' or 'nav_executor'
                    for agent_name in self.agents_map.keys()
                    if agent_name.endswith("_nav_agent") or agent_name.endswith("_nav_executor")
                ]
            )
        )

        group_participants_names = [f"{agent_name}_nav_agent" for agent_name in nav_agents_names] + [f"{agent_name}_nav_executor" for agent_name in nav_agents_names]

        def state_transition(last_speaker, groupchat) -> autogen.ConversableAgent | None:  # type: ignore
            last_message = groupchat.messages[-1]["content"]
            target_helper = extract_target_helper(last_message)

            if "##TERMINATE TASK##" in last_message.strip():
                return None

            if last_speaker is self.agents_map["user"]:
                if target_helper in nav_agents_names:
                    return self.agents_map[f"{target_helper}_nav_agent"]
                return None
            elif last_speaker in [self.agents_map[f"{agent_name}_nav_agent"] for agent_name in nav_agents_names]:
                # Get the base name by removing '_nav_agent' suffix
                base_name = last_speaker.name.rsplit("_nav_agent", 1)[0]
                return self.agents_map[f"{base_name}_nav_executor"]
            else:
                # Get the base name by removing '_nav_executor' suffix
                base_name = last_speaker.name.rsplit("_nav_executor", 1)[0]
                return self.agents_map[f"{base_name}_nav_agent"]

        gm_llm_config = {
            "config_list": self.planner_agent_model_config,
            **self.planner_agent_config["llm_config_params"],
        }

        groupchat = autogen.GroupChat(
            agents=[self.agents_map[agent_name] for agent_name in group_participants_names],
            messages=[],
            max_round=self.planner_number_of_rounds,
            # select_speaker_auto_verbose=True,
            speaker_selection_method=state_transition,
        )

        manager = autogen.GroupChatManager(
            groupchat=groupchat,
            llm_config=gm_llm_config,
        )  # type: ignore

        with Cache.disk(cache_seed=5) as cache:
            self.agents_map["user"].register_nested_chats(
                [
                    {
                        "sender": self.agents_map["user"],
                        "recipient": manager,
                        "message": reflection_message,
                        "max_turns": 1,
                        "summary_method": my_custom_summary_method,
                    }
                ],
                trigger=trigger_nested_chat,
                cache=cache,
            )
        return self

    @classmethod
    def convert_model_config_to_autogen_format(cls, model_config: dict[str, str]) -> list[dict[str, Any]]:
        env_var: list[dict[str, str]] = [model_config]
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as temp:
            json.dump(env_var, temp)
            temp_file_path = temp.name

        return autogen.config_list_from_json(env_or_file=temp_file_path)

    def get_chat_logs_dir(self) -> str | None:
        """
        Get the directory for saving chat logs with timestamp.

        Returns:
            str|None: The directory path or None if there is not one
        """
        # Get paths from config with timestamp
        return get_global_conf().get_source_log_folder_path(self.stake_id)

    def set_chat_logs_dir(self, chat_logs_dir: str) -> None:
        """
        Set the directory for saving chat logs.

        Args:
            chat_logs_dir (str): The directory path.

        """
        self.chat_logs_dir = chat_logs_dir

    def __save_chat_log(self, sender: autogen.ConversableAgent, receiver: autogen.ConversableAgent) -> None:
        messages_str_keys = {str(key): value for key, value in sender.chat_messages.items()}  # type: ignore
        res_output_thoughts_logs_di = {}
        for key, value in messages_str_keys.items():
            if res_output_thoughts_logs_di.get(sender.agent_name):
                res_output_thoughts_logs_di[sender.agent_name] += value
            else:
                res_output_thoughts_logs_di[sender.agent_name] = value

        for key, vals in res_output_thoughts_logs_di.items():
            for idx, val in enumerate(vals):

                logger.debug(f"{sender.name} chat log: {val}")
                content = val["content"]
                if isinstance(content, str):
                    content = content.replace("```json", "").replace("```", "").strip()
                    res_content = None
                    try:
                        res_content = json.loads(content)
                    except json.JSONDecodeError:
                        logger.debug(f"Failed to decode JSON: {content}, keeping as multiline string")
                        res_content = content
                elif isinstance(content, dict):
                    res_content = content
                elif isinstance(content, list):
                    if isinstance(content[0], dict):
                        res_content = content
                else:
                    res_content = content
                res_output_thoughts_logs_di[key][idx]["content"] = process_chat_message_content(val["content"])
        if not self.save_chat_logs_to_files:
            logger.info(
                "Nested chat logs",
                extra={f"log_between_sender_{sender.name}_rec_{receiver.name}": res_output_thoughts_logs_di},
            )
        else:
            chat_logs_file = os.path.join(
                self.get_chat_logs_dir() or "",
                f"log_between_sender-{sender.name}-rec-{receiver.name}_{str(datetime.datetime.now().strftime('%Y-%m-%dT%H-%M-%S-%f'))}.json",
            )
            # Save the chat log to a file
            with open(chat_logs_file, "w") as file:
                json.dump(res_output_thoughts_logs_di, file, indent=4)

    # def __save_chat_log(self, chat_log: list[dict[str, Any]]) -> None:
    #     if not self.save_chat_logs_to_files:
    #         logger.info("Nested chat logs", extra={"nested_chat_log": chat_log})
    #     else:
    #         chat_logs_file = os.path.join(self.get_chat_logs_dir() or "", f"nested_chat_log_{str(time_ns())}.json")
    #         # Save the chat log to a file
    #         with open(chat_logs_file, "w") as file:
    #             json.dump(chat_log, file, indent=4)

    async def __initialize_agents(self) -> dict[str, autogen.ConversableAgent]:
        """
        Instantiate all agents with their appropriate prompts/tools.

        Returns:
            dict: A dictionary of agent instances.

        """
        agents_map: dict[str, UserProxyAgent_SequentialFunctionExecution | autogen.ConversableAgent] = {}
        agents_map["user"] = await self.__create_user_delegate_agent()
        agents_map["browser_nav_executor"] = self.__create_browser_nav_executor_agent()
        agents_map["browser_nav_agent"] = self.__create_browser_nav_agent(agents_map["browser_nav_executor"])
        agents_map["api_nav_executor"] = self.__create_api_nav_executor_agent()
        agents_map["api_nav_agent"] = self.__create_api_nav_agent(agents_map["api_nav_executor"])
        agents_map["sec_nav_executor"] = self.__create_sec_nav_executor_agent()
        agents_map["sec_nav_agent"] = self.__create_sec_nav_agent(agents_map["sec_nav_executor"])
        agents_map["sql_nav_executor"] = self.__create_sql_nav_executor_agent()
        agents_map["sql_nav_agent"] = self.__create_sql_nav_agent(agents_map["sql_nav_executor"])
        agents_map["static_waiter_nav_executor"] = self.__create_static_waiter_nav_executor_agent()
        agents_map["static_waiter_nav_agent"] = self.__create_static_waiter_nav_agent(agents_map["static_waiter_nav_executor"])
        agents_map["planner_agent"] = self.__create_planner_agent(agents_map["user"])
        return agents_map

    async def __create_user_delegate_agent(self) -> autogen.ConversableAgent:
        """
        Create a ConversableAgent instance.

        Returns:
            autogen.ConversableAgent: An instance of ConversableAgent.

        """

        def is_planner_termination_message(x: dict[str, str]) -> bool:  # type: ignore
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
                    content_json = parse_response(content)
                    _terminate = content_json.get("terminate", "no")
                    final_response = content_json.get("final_response", None)
                    if _terminate == "yes":
                        should_terminate = True
                        if final_response:
                            notify_planner_messages(final_response, message_type=MessageType.ANSWER)
                except json.JSONDecodeError:
                    logger.error("Error decoding JSON response:\n{content}.\nTerminating..")
                    should_terminate = True

            return should_terminate  # type: ignore

        task_delegate_agent = UserProxyAgent_SequentialFunctionExecution(
            name="user",
            llm_config=False,
            system_message=LLM_PROMPTS["USER_AGENT_PROMPT"],
            is_termination_msg=is_planner_termination_message,  # type: ignore
            human_input_mode="NEVER",
            max_consecutive_auto_reply=self.planner_number_of_rounds,
        )
        return task_delegate_agent

    def __create_browser_nav_executor_agent(self) -> autogen.UserProxyAgent:
        """
        Create a UserProxyAgent instance for executing browser control.

        Returns:
            autogen.UserProxyAgent: An instance of UserProxyAgent.

        """

        def is_browser_executor_termination_message(x: dict[str, str]) -> bool:  # type: ignore

            tools_call: Any = x.get("tool_calls", "")
            if tools_call:
                chat_messages = self.agents_map["browser_nav_executor"].chat_messages  # type: ignore
                # Get the only key from the dictionary
                agent_key = next(iter(chat_messages))  # type: ignore
                # Get the chat messages corresponding to the only key
                messages = chat_messages[agent_key]  # type: ignore
                return is_agent_stuck_in_loop(messages)  # type: ignore
            else:
                logger.info("Terminating browser executor")
                return True

        browser_nav_executor_agent = UserProxyAgent_SequentialFunctionExecution(
            name="browser_nav_executor",
            is_termination_msg=is_browser_executor_termination_message,
            human_input_mode="NEVER",
            llm_config=None,
            max_consecutive_auto_reply=self.nav_agent_number_of_rounds,
            code_execution_config={
                "last_n_messages": 1,
                "work_dir": "tasks",
                "use_docker": False,
            },
        )
        logger.info(">>> Created browser_nav_executor_agent: %s", browser_nav_executor_agent)
        return browser_nav_executor_agent

    def __create_browser_nav_agent(self, user_proxy_agent: UserProxyAgent_SequentialFunctionExecution) -> autogen.ConversableAgent:
        """
        Create a BrowserNavAgent instance.

        Args:
            user_proxy_agent (autogen.UserProxyAgent): The instance of UserProxyAgent that was created.

        Returns:
            autogen.AssistantAgent: An instance of BrowserNavAgent.

        """
        browser_nav_agent = BrowserNavAgent(
            self.browser_nav_agent_model_config,
            self.nav_agent_config["llm_config_params"],  # type: ignore
            self.nav_agent_config["other_settings"].get("system_prompt", None),
            user_proxy_agent,
        )  # type: ignore
        return browser_nav_agent.agent

    def __create_api_nav_executor_agent(self) -> autogen.UserProxyAgent:
        """
        Create a UserProxyAgent instance for executing browser control.

        Returns:
            autogen.UserProxyAgent: An instance of UserProxyAgent.

        """

        def is_api_executor_termination_message(x: dict[str, str]) -> bool:  # type: ignore

            tools_call: Any = x.get("tool_calls", "")
            if tools_call:
                chat_messages = self.agents_map["api_nav_executor"].chat_messages  # type: ignore
                # Get the only key from the dictionary
                agent_key = next(iter(chat_messages))  # type: ignore
                # Get the chat messages corresponding to the only key
                messages = chat_messages[agent_key]  # type: ignore
                return is_agent_stuck_in_loop(messages)  # type: ignore
            else:
                logger.info("Terminating api executor")
                return True

        api_nav_executor_agent = UserProxyAgent_SequentialFunctionExecution(
            name="api_nav_executor",
            is_termination_msg=is_api_executor_termination_message,
            human_input_mode="NEVER",
            llm_config=None,
            max_consecutive_auto_reply=self.nav_agent_number_of_rounds,
            code_execution_config={
                "last_n_messages": 1,
                "work_dir": "tasks",
                "use_docker": False,
            },
        )
        logger.info(">>> Created api_nav_executor_agent: %s", api_nav_executor_agent)
        return api_nav_executor_agent

    def __create_api_nav_agent(self, user_proxy_agent: UserProxyAgent_SequentialFunctionExecution) -> autogen.ConversableAgent:
        """
        Create a ApiNavAgent instance.

        Args:
            user_proxy_agent (autogen.UserProxyAgent): The instance of UserProxyAgent that was created.

        Returns:
            autogen.AssistantAgent: An instance of ApiNavAgent.

        """
        api_nav_agent = ApiNavAgent(
            self.api_nav_agent_model_config,
            self.nav_agent_config["llm_config_params"],  # type: ignore
            self.nav_agent_config["other_settings"].get("system_prompt", None),
            user_proxy_agent,
        )  # type: ignore
        return api_nav_agent.agent

    def __create_sec_nav_executor_agent(self) -> autogen.UserProxyAgent:
        """
        Create a UserProxyAgent instance for executing browser control.

        Returns:
            autogen.UserProxyAgent: An instance of UserProxyAgent.

        """

        def is_api_executor_termination_message(x: dict[str, str]) -> bool:  # type: ignore

            tools_call: Any = x.get("tool_calls", "")
            if tools_call:
                chat_messages = self.agents_map["api_nav_executor"].chat_messages  # type: ignore
                # Get the only key from the dictionary
                agent_key = next(iter(chat_messages))  # type: ignore
                # Get the chat messages corresponding to the only key
                messages = chat_messages[agent_key]  # type: ignore
                return is_agent_stuck_in_loop(messages)  # type: ignore
            else:
                logger.info("Terminating api sec executor")
                return True

        api_nav_executor_agent = UserProxyAgent_SequentialFunctionExecution(
            name="sec_nav_executor",
            is_termination_msg=is_api_executor_termination_message,
            human_input_mode="NEVER",
            llm_config=None,
            max_consecutive_auto_reply=self.nav_agent_number_of_rounds,
            code_execution_config={
                "last_n_messages": 1,
                "work_dir": "tasks",
                "use_docker": False,
            },
        )
        logger.info(">>> Created api_nav_executor_agent: %s", api_nav_executor_agent)
        return api_nav_executor_agent

    def __create_sec_nav_agent(self, user_proxy_agent: UserProxyAgent_SequentialFunctionExecution) -> autogen.ConversableAgent:
        """
        Create a ApiNavAgent instance.

        Args:
            user_proxy_agent (autogen.UserProxyAgent): The instance of UserProxyAgent that was created.

        Returns:
            autogen.AssistantAgent: An instance of ApiNavAgent.

        """
        sec_nav_agent = SecNavAgent(
            self.sec_nav_agent_model_config,
            self.nav_agent_config["llm_config_params"],  # type: ignore
            self.nav_agent_config["other_settings"].get("system_prompt", None),
            user_proxy_agent,
        )  # type: ignore
        return sec_nav_agent.agent

    def __create_sql_nav_agent(self, user_proxy_agent: UserProxyAgent_SequentialFunctionExecution) -> autogen.ConversableAgent:
        """
        Create a SqlNavAgent instance.

        Args:
            user_proxy_agent (autogen.UserProxyAgent): The instance of UserProxyAgent that was created.

        Returns:
            autogen.AssistantAgent: An instance of SqlNavAgent.

        """
        sql_nav_agent = SqlNavAgent(
            self.sql_nav_agent_model_config,
            self.nav_agent_config["llm_config_params"],  # type: ignore
            self.nav_agent_config["other_settings"].get("system_prompt", None),
            user_proxy_agent,
        )  # type: ignore
        return sql_nav_agent.agent

    def __create_sql_nav_executor_agent(self) -> autogen.UserProxyAgent:
        """
        Create a UserProxyAgent instance for executing browser control.

        Returns:
            autogen.UserProxyAgent: An instance of UserProxyAgent.

        """

        def is_sql_executor_termination_message(x: dict[str, str]) -> bool:  # type: ignore

            tools_call: Any = x.get("tool_calls", "")
            if tools_call:
                chat_messages = self.agents_map["sql_nav_executor"].chat_messages  # type: ignore
                # Get the only key from the dictionary
                agent_key = next(iter(chat_messages))  # type: ignore
                # Get the chat messages corresponding to the only key
                messages = chat_messages[agent_key]  # type: ignore
                return is_agent_stuck_in_loop(messages)  # type: ignore
            else:
                logger.info("Terminating sql executor")
                return True

        sql_nav_executor_agent = UserProxyAgent_SequentialFunctionExecution(
            name="sql_nav_executor",
            is_termination_msg=is_sql_executor_termination_message,
            human_input_mode="NEVER",
            llm_config=None,
            max_consecutive_auto_reply=self.nav_agent_number_of_rounds,
            code_execution_config={
                "last_n_messages": 1,
                "work_dir": "tasks",
                "use_docker": False,
            },
        )
        logger.info(">>> Created sql_nav_executor_agent: %s", sql_nav_executor_agent)
        return sql_nav_executor_agent

    def __create_static_waiter_nav_executor_agent(self) -> autogen.UserProxyAgent:
        """
        Create a UserProxyAgent instance for executing static wait operations.

        Returns:
            autogen.UserProxyAgent: An instance of UserProxyAgent.
        """

        def is_static_waiter_executor_termination_message(x: dict[str, str]) -> bool:  # type: ignore
            tools_call: Any = x.get("tool_calls", "")
            if tools_call:
                chat_messages = self.agents_map["static_waiter_nav_executor"].chat_messages  # type: ignore
                agent_key = next(iter(chat_messages))  # type: ignore
                messages = chat_messages[agent_key]  # type: ignore
                return is_agent_stuck_in_loop(messages)  # type: ignore
            else:
                logger.info("Terminating static waiter executor")
                return True

        static_waiter_nav_executor_agent = UserProxyAgent_SequentialFunctionExecution(
            name="static_waiter_nav_executor",
            is_termination_msg=is_static_waiter_executor_termination_message,
            human_input_mode="NEVER",
            llm_config=None,
            max_consecutive_auto_reply=self.nav_agent_number_of_rounds,
            code_execution_config={
                "last_n_messages": 1,
                "work_dir": "tasks",
                "use_docker": False,
            },
        )
        logger.info(">>> Created static_waiter_nav_executor_agent: %s", static_waiter_nav_executor_agent)
        return static_waiter_nav_executor_agent

    def __create_static_waiter_nav_agent(self, user_proxy_agent: UserProxyAgent_SequentialFunctionExecution) -> autogen.ConversableAgent:
        """
        Create a StaticWaiterNavAgent instance.

        Args:
            user_proxy_agent (autogen.UserProxyAgent): The instance of UserProxyAgent that was created.

        Returns:
            autogen.AssistantAgent: An instance of StaticWaiterNavAgent.
        """
        static_waiter_nav_agent = StaticWaiterNavAgent(
            self.static_waiter_nav_agent_model_config,
            self.nav_agent_config["llm_config_params"],  # type: ignore
            self.nav_agent_config["other_settings"].get("system_prompt", None),
            user_proxy_agent,
        )  # type: ignore
        return static_waiter_nav_agent.agent

    def __create_planner_agent(self, assistant_agent: autogen.ConversableAgent) -> autogen.ConversableAgent:
        """
        Create a Planner Agent instance. This is mainly used for exploration at this point

        Returns:
            autogen.AssistantAgent: An instance of PlannerAgent.

        """
        planner_agent = PlannerAgent(
            self.planner_agent_model_config,
            self.planner_agent_config["llm_config_params"],  # type: ignore
            self.planner_agent_config["other_settings"].get("system_prompt", None),
            assistant_agent,
        )  # type: ignore
        return planner_agent.agent

    async def clean_up_plan(self) -> None:
        """
        Clean up the plan after each command is processed.

        """
        # await self.__initialize_agents()
        self.agents_map["planner_agent"].clear_history()
        self.agents_map["user"].clear_history()
        logger.info("Plan cleaned up.")

    async def process_command(self, command: str, *args: Any, current_url: str | None = None, **kwargs: Any) -> autogen.ChatResult | None:
        """
        Process a command by sending it to one or more agents.

        Args:
            command (str): The command to be processed.
            current_url (str, optional): The current URL of the browser. Defaults to None.

        Returns:
            autogen.ChatResult | None: The result of the command processing, or None if an error occurred. Contains chat log, cost(tokens/price)

        """
        current_url_prompt_segment = ""
        if current_url:
            current_url_prompt_segment = f"Current Page: {current_url}"

        prompt = Template(LLM_PROMPTS["COMMAND_EXECUTION_PROMPT"]).substitute(command=command, current_url_prompt_segment=current_url_prompt_segment)
        logger.info("Prompt for command: %s", prompt)
        with Cache.disk(cache_seed=5) as cache:
            try:
                if self.agents_map is None:
                    raise ValueError("Agents map is not initialized.")

                result = await self.agents_map["user"].a_initiate_chat(  # type: ignore
                    self.agents_map["planner_agent"],  # self.manager # type: ignore
                    max_turns=self.planner_number_of_rounds,
                    # clear_history=True,
                    message=prompt,
                    silent=False,
                    cache=cache,
                )
                # reset usage summary for all agents after each command
                # for agent in self.agents_map.values():
                #     if hasattr(agent, "client") and agent.client is not None:
                #         agent.client.clear_usage_summary()  # type: ignore
                return result
            except openai.BadRequestError as bre:
                logger.error('Unable to process command: "%s". %s', command, bre)
                traceback.print_exc()
            return None
