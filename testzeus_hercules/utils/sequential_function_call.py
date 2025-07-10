import asyncio
import inspect
import json
from typing import Any

from autogen import Agent  # type: ignore
from autogen import UserProxyAgent  # type: ignore

from testzeus_hercules.integration.dual_mode_adapter import get_dual_mode_adapter


class UserProxyAgent_SequentialFunctionExecution(UserProxyAgent):
    def __init__(self, *args, **kwargs):  # type: ignore
        self.agent_name = "user_proxy_agent"
        super().__init__(*args, **kwargs)  # type: ignore
        # position = 2 allows termination check to be called earlier, this helps detect loops.
        self.register_reply(Agent, UserProxyAgent_SequentialFunctionExecution.sequential_generate_tool_calls_reply, position=2)  # type: ignore

    def sequential_generate_tool_calls_reply(  # type: ignore
        self,
        messages: list[dict] | None = None,  # type: ignore
        sender: Agent | None = None,
        config: Any | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Generate a reply using tool call."""
        if config is None:
            config = self
        if messages is None:
            messages = self._oai_messages[sender]  # type: ignore
        message = messages[-1]  # type: ignore
        tool_returns = []
        skip_flag: bool = False
        for tool_call in message.get("tool_calls", []):  # type: ignore
            function_call = tool_call.get("function", {})  # type: ignore
            func = self._function_map.get(function_call.get("name", None), None)  # type: ignore
            func_return = None
            if inspect.iscoroutinefunction(func):  # type: ignore
                try:
                    # get the running loop if it was already created
                    loop = asyncio.get_running_loop()
                    close_loop = False
                except RuntimeError:
                    # create a loop if there is no running loop
                    loop = asyncio.new_event_loop()
                    close_loop = True
                if not skip_flag:
                    _, func_return = loop.run_until_complete(self.a_execute_function(function_call))  # type: ignore
                    
                    try:
                        adapter = get_dual_mode_adapter()
                        tool_name = function_call.get("name", "unknown")
                        
                        args = function_call.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except:
                                args = {}
                        
                        selector = args.get("selector", args.get("url", args.get("query", args.get("sql", args.get("endpoint", "unknown")))))
                        
                        success = func_return is not None and isinstance(func_return, dict) and func_return.get("content", "") != ""
                        
                        loop.run_until_complete(adapter.log_tool_interaction(
                            tool_name=tool_name,
                            selector=str(selector),
                            action="execute",
                            success=success,
                            element_info=args,
                            error_message=func_return.get("content") if not success and isinstance(func_return, dict) else None
                        ))
                    except Exception as e:
                        pass
                    
                    if close_loop:
                        loop.close()
            else:
                if not skip_flag:
                    _, func_return = self.execute_function(function_call)  # type: ignore
                    
                    try:
                        adapter = get_dual_mode_adapter()
                        tool_name = function_call.get("name", "unknown")
                        
                        args = function_call.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except:
                                args = {}
                        
                        selector = args.get("selector", args.get("url", args.get("query", args.get("sql", args.get("endpoint", "unknown")))))
                        
                        success = func_return is not None and isinstance(func_return, dict) and func_return.get("content", "") != ""
                        
                        try:
                            loop = asyncio.get_running_loop()
                            loop.run_until_complete(adapter.log_tool_interaction(
                                tool_name=tool_name,
                                selector=str(selector),
                                action="execute",
                                success=success,
                                element_info=args,
                                error_message=func_return.get("content") if not success and isinstance(func_return, dict) else None
                            ))
                        except RuntimeError:
                            asyncio.run(adapter.log_tool_interaction(
                                tool_name=tool_name,
                                selector=str(selector),
                                action="execute",
                                success=success,
                                element_info=args,
                                error_message=func_return.get("content") if not success and isinstance(func_return, dict) else None
                            ))
                    except Exception as e:
                        pass
            if func_return is None:  # type: ignore
                if skip_flag:
                    content = (
                        "VERY IMPORTANT: This function could not be executed since previous function resulted in a Task change the state. You must get current state and repeat the function if needed."
                    )
                else:
                    content = ""
            else:
                content = func_return.get("content", "")  # type: ignore

            if content is None:
                content = ""

            if "as a consequence of this action" in content.lower():  # type: ignore
                skip_flag = True

            tool_call_id = tool_call.get("id", None)  # type: ignore
            if tool_call_id is not None:
                tool_call_response = {  # type: ignore
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "content": content,
                }
            else:
                tool_call_response = {  # type: ignore
                    "role": "tool",
                    "content": content,
                }
            tool_returns.append(tool_call_response)  # type: ignore

        if tool_returns:
            return True, {
                "role": "tool",
                "tool_responses": tool_returns,
                "content": "\n\n".join([self._str_for_tool_response(tool_return) for tool_return in tool_returns]),  # type: ignore
            }
        return False, None
