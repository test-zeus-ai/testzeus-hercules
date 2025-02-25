import os
from datetime import datetime
from string import Template
from typing import Any, Optional

import autogen  # type: ignore
from autogen import ConversableAgent  # type: ignore
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.memory.dynamic_ltm import DynamicLTM
from testzeus_hercules.core.memory.static_ltm import get_user_ltm
from testzeus_hercules.core.post_process_responses import (
    final_reply_callback_planner_agent as print_message_as_planner,  # type: ignore
)
from testzeus_hercules.utils.logger import logger


class PlannerAgent:
    prompt = """# Test EXECUTION Task Planner, YOU ARE TESTING THE APPLICATION

You are a test EXECUTION task planner that processes Gherkin BDD feature tasks and executes them through a helper.

## Core Responsibilities
- Parse Gherkin BDD features and create VERY DETAILED EXPANDED step-by-step execution plans
- THE PLAN SHOULD BE AS DETAILED AS POSSIBLE, INCLUDING ALL STEPS
- ASSUMPTION AGAINST INPUTS SHOULD BE AVOIDED.
- Include assertion validation in subtasks
- Delegate atomic operations to helper
- Analyze helper responses before proceeding
- Ensure successful task completion or detailed failure reporting
- Expand the plan to fullest considering test data, unroll the loops as per test data
- Stick to the test case and test data provided while building the plan.
- target_helper should be as per the next step operation.
- ALL INFORMATION TO BE PASSED TO THE HELPER SHOULD BE IN THE NEXT_STEP IF IN MIDDLE OF PLAN EXECUTION
- NEVER PASS TASK HELPER IN NEXT_STEP, ONLY PASS IN TARTGET_HELPER

## Response Format
Must return well-formatted JSON with:
{
"plan": "VERY DETAILED EXPANDED plan (step-by-step with step numbers) stick to user task input AS CORE BUT HAVE LIBERTY TO EXPAND, ALL IN STRING FORMAT",
"next_step": "Atomic operation for helper, AS PER ONLY PLAN and ALWAYS COURSE CORRECT, CAN ADD DATA TO NEXT_STEP IF NEEDED, ALL IN STRING FORMAT",
"terminate": "'yes' when complete/failed, 'no' during iterations",
"final_response": "Task outcome (only when terminate='yes')",
"is_assert": "boolean - if current step is assertion",
"assert_summary": "EXPECTED RESULT: x\\nACTUAL RESULT: y (required if is_assert=true)",
"is_passed": "boolean - assertion success status",
"target_helper": "'browser'|'api'|'sec'|'sql'|'time_keeper'|'Not_Applicable'"
}

## Helper Capabilities
- Browser: Page navigation, element interaction, state verification
- API: Endpoint interactions, response handling
- Security: Security testing constructs
- SQL: Intent-based database operations
- Time Keeper: Pauses execution for specified duration in seconds, and perform time related operations
- All helpers are stateless and handle one operation at a time
- file interactions can be done by navigation agents.

## Key Guidelines
1. Task Execution:
- Break into atomic operations considering test data
- Verify each step's completion
- Handle mandatory fields/parameters
- Close unexpected popups
- Wait for page loads

2. Data Management:
- Store context within same helper when needed
- Validate test data availability
- Handle iterations as individual validated steps
- Pass relevant data between sequential operations

3. Validation:
- Verify features before using
- Confirm successful completion
- Include comprehensive assertion checks
- Re-verify critical conditions

4. Navigation:
- Use direct URLs when known
- Handle pagination
- Manage dynamic elements
- Optimize search queries

5. Error Handling:
- Revise failed approaches
- Provide detailed failure summaries
- Handle unexpected states
- Terminate on assertion failures

## Helper-Specific Guidelines
Browser Operations:
- Confirm mandatory fields
- Handle filtering/sorting
- Manage dynamic content
- Navigate pagination
- Close popups
- accessibility 

API Operations:
- Validate required parameters
- Handle response states
- Manage data flow between calls
- accessibility 

Security Operations:
- Verify testing constructs
- Handle security response states
- Maintain data continuity

Database Operations:
- Provide operation intent only
- Let helper construct queries
- Handle complex joins
- Verify result validity

## Critical Rules
1. One atomic operation per step
2. Always verify before terminating
3. Maintain state within same helper
4. Terminate on assertion failures
5. Never assume feature existence
6. Handle all iterations completely
7. Provide detailed failure summaries
8. Make independent termination decisions
9. Return single JSON response
10. No duplicate JSON keys
11. Termination scenario should always be an assert.
12. Never provide explination or notes only JSON response.
13. Don't take unnecessary waits. Validate efficiently.
14. MUST BE EFFICIENT IN EXECUTION AND PLANNING.

Available Test Data: $basic_test_information
"""

    def __init__(self, model_config_list, llm_config_params: dict[str, Any], system_prompt: str | None, user_proxy_agent: ConversableAgent):  # type: ignore
        """
        Initialize the PlannerAgent and store the AssistantAgent instance
        as an instance attribute for external access.

        Parameters:
        - model_config_list: A list of configuration parameters required for AssistantAgent.
        - llm_config_params: A dictionary of configuration parameters for the LLM.
        - system_prompt: The system prompt to be used for this agent or the default will be used if not provided.
        - user_proxy_agent: An instance of the UserProxyAgent class.
        """
        user_ltm = self.get_ltm()
        system_message = self.prompt

        if system_prompt and len(system_prompt) > 0:
            if isinstance(system_prompt, list):
                system_message = "\n".join(system_prompt)
            else:
                system_message = system_prompt
            logger.info(
                f"Using custom system prompt for PlannerAgent: {system_message}"
            )

        config = get_global_conf()
        if (
            not config.should_use_dynamic_ltm() and user_ltm
        ):  # Use static LTM when dynamic is disabled
            user_ltm = "\n" + user_ltm
            system_message = Template(system_message).substitute(
                basic_test_information=user_ltm
            )
        system_message = (
            system_message
            + "\n"
            + f"Current timestamp is {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(f"Planner agent using model: {model_config_list[0]['model']}")

        self.agent = autogen.AssistantAgent(
            name="planner_agent",
            system_message=system_message,
            llm_config={
                "config_list": model_config_list,
                **llm_config_params,  # unpack all the name value pairs in llm_config_params as is
            },
        )
        # add_text_compressor(self.agent)

        def ingest_message_in_memory(
            recipient: autogen.ConversableAgent,
            messages: Optional[list[dict]] = None,
            sender: Optional[autogen.Agent] = None,
            config: Optional[dict[str, Any]] = None,
        ) -> tuple[bool, Any]:
            if messages:
                for message_list in messages:
                    for key, message in message_list.items():
                        DynamicLTM().incoming_chat(message)
                        print_message_as_planner(message=message)
            return False, None

        self.agent.register_reply(  # type: ignore
            [autogen.AssistantAgent, None],
            reply_func=ingest_message_in_memory,
            config={"callback": None},
            ignore_async_in_sync_chat=False,
        )

    def get_ltm(self) -> str | None:
        """
        Get the the long term memory of the user.
        returns: str | None - The user LTM or None if not found.
        """
        return get_user_ltm()
