how to start with?

approach 1:
using pypi package

pip install testzeus-hercules
followed by
playwright install --with-deps

as the agent uses playwright to interact with web pages

once installed you will need 5 basic parms to be provided to trigger it

 --input-file INPUT_FILE
                        Path to the input gherkin feature file, to be tested.
  --output-path OUTPUT_PATH
                        Path to the output directory. the path of junitxml result and html report for the test run.
  --test-data-path TEST_DATA_PATH
                        Path to the test data directory. the path where agent expects test data to be present, all test data used in feature testing should be present here.
  --project-base PROJECT_BASE
                        Path to the project base directory. this is an optional param, if you populate this --input-file, --output-path, --test-data-path is not required and agent will assume all the 3 folders exist in the following format inside the project base.
├── gherkin_files
├── input
│   └── test.feature
├── log_files
├── output
│   ├── test.feature_result.html
│   └── test.feature_result.xml
├── proofs
│   └── User_opens_Google_homepage
│       ├── network_logs.json
│       ├── screenshots
│       └── videos
└── test_data
    └── test_data.txt

  --llm-model LLM_MODEL
                        Name of the LLM model to be used by the the agent, recommended is gpt-4o but it can take others
  --llm-model-api-key LLM_MODEL_API_KEY
                        API key for the LLM model, api_key of the llm provider to access llm APIs, something like sk-proj-k.......


after passing all the data the command to trigger should look like this

testzeus-hercules --input-file opt/input/test.feature --output-path opt/output --test-data-path opt/test_data --llm-model gpt-4o --llm-model-api-key sk-proj-k.......

now trigger the command, you can expect the execution to start and try to open web browser by default chrome,
the agent will prepare the plan of execution based on the feature file steps provided. The plan internally will expand the brief steps mentioned in the feature file and create a elaborated version of it, the agent will also detect the asserts in the feature file and plans the validation of expected result from feature file with the execution result happening during the execution. All the steps once elaborated are passed to different tools based on the type of execution requiremet of the step. for example if the elaborated step wants to click on a button and capture the feedback it will be passed to the click_using_selector tool

once the execution is completed there will be lots of logs explaining what were the sequence of events, as a consumer the best place to start with is the output-path, this will have the junitxml result file as well an html report regarding the testcase execution.
beyond this you can also find proofs of execution such as video recording, screenshots per event and network logs in the proofs folder.

to go deeper and understand the Chain of thoughts refer to the chat_messages.json in the log_files, this will have exact steps that are planned by the agent.



<some feature file screen shot>

<some result screent shot>

so now that we are set with the simplest approach, lets see what are other ways beyond running via Pypi package.




the Docker way for all the scale lovers.

testzeuls-hercules is also available as a docker image in docker hub, you can pull the image via


docker pull testzeusl/hercules:latest or whatever version that make sense.

following that

you can run the same process but within container using 
docker run --env-file=.env -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json -v ./opt:/testzeus-hercules/opt --rm -it testzeus/hercules:latest

here the similar scope applies, all the required envrionment varaibles can be set by passing env file to the docker run command
in case if you are planning to complete control on the agent and which LLM to used beyond the ones that are provided by openAI, then you can opt passing agents_llm_config.json as a mount to the container. Its for advance usecase and for beginer it is not required. refer to sample files .env-example and agents_llm_config-example.json for details and reference.

you should mount the opt folder to the docker run so that all the inputs can be passed to the agent running inside the docker container and the output can be pulled out for further processing. The repository has a sample opt folder that can be mounted easily.

in the docker case there is no need for using --input-file, --output-path, --test-data-path, --project-base as they all are already been handled by mounting the opt folder in the docker run command.

While running in docker mode, you should understand that the agent has only access to headless web browser, in case if you want the agent to connect to visible web browser, then you should try the CDP url option in the environment file, that option can help you to connect to the existing browser in cloud or in the host-machine.

after the command completion the container terminates and output is written in the mounted opt folder, in the same way as followed.
opt
├── gherkin_files
├── input
│   └── test.feature
├── log_files
├── output
│   ├── test.feature_result.html
│   └── test.feature_result.xml
├── proofs
│   └── User_opens_Google_homepage
│       ├── network_logs.json
│       ├── screenshots
│       └── videos
└── test_data
    └── test_data.txt



ok so in case if you are an hardcore enthusiast lets start with how we can use testzeus-hercules via the source code and get complete expirecen of customisation and extending the agent with more tools.

to begain with

make sure you have python3.11 in your system before moving further.

then
git clone git@github.com:test-zeus-ai/testzeus-hercules.git

then
cd testzeus-hercules

to start the process use the handy "make commands" in the repo.
use make help for checking out possible options.

installing poetry
make setup-poetry

building and installing dependecies for testzeus-hercules
make install

and we are good to go. To run testzeus-hercules from source run.
make run


the above command reads the relevant feature files from the opt folder and executes and put the output in the same folder, the opt folder has following format
opt
├── gherkin_files
├── input
│   └── test.feature
├── log_files
├── output
│   ├── test.feature_result.html
│   └── test.feature_result.xml
├── proofs
│   └── User_opens_Google_homepage
│       ├── network_logs.json
│       ├── screenshots
│       └── videos
└── test_data
    └── test_data.txt



you can also run the agent in the interactive as a instruction execution agent, which is more useful for RPA and debugging testcases and agent behaviour on new envornments, while building new tooling and extending the agents. run

make run-interactive

this will trigger a input prompt where basically you can chat with the agent and the agent will perform actions based on your commands.


now lets see how we can configure the agent in detail

understainfg envrionment file (.env)
to configure in detail copy the base env file .env-example to .env
the agent is capable of running in 2 configuration forms.

1) same LLM as a backbone for all the activities within the agent, in such case initialising LLM_MODEL_NAME, LLM_MODEL_API_KEY values are sufficient. if using non OPENAI hosted solution but still OPENAI LLMs something like openAI via groq, then pass the LLM_MODEL_BASE_URL url as well.
2) in case if you are planning to configure local LLMs or non openAI LLMs then use the other params like AGENTS_LLM_CONFIG_FILE, AGENTS_LLM_CONFIG_FILE_REF_KEY, theses are powerful options and can affect the quality of the agent outputs.

the agent while execution consider a base folder that is by default ./opt but can be changed by env variable PROJECT_SOURCE_ROOT

Connecting the agent an existing Chrome instance in local or remote, this is extremly useful when you are running agent in docker way for scale. This way you can connect the agent running in your infra to remote browser like browserbase or your self hosted grid. use CDP_ENDPOINT_URL to set the CDP url of the chrome that has to be connected to the agent.

you can control other behaviours of the agent based on following env variables. the names are self explinatory
HEADLESS=true
RECORD_VIDEO=false
TAKE_SCREENSHOTS=false
BROWSER_TYPE=chromium # firefox, chromium
CAPTURE_NETWORK=false


understanding agents_llm_config-example.json, its a list of configs of LLM that you want to provide the agent.
	"mistral-large-agente": {
		"planner_agent": {
			"model_name": "mistral",
			"model_api_key": "",
			"model_base_url": "https://...",
            "system_prompt": "You are a web automation task planner....",
            "llm_config_params": {
                "cache_seed": null,
                "temperature": 0.1,
                "top_p": 0.1
            }
		},
		"browser_nav_agent": {
			"model_name": "mistral",
			"model_api_key": "",
			"model_base_url": "https://...",
			"system_prompt": "You will perform web navigation tasks with the functions that you have...\nOnce a task is completed, confirm completion with ##TERMINATE TASK##.",
            "llm_config_params": {
                "cache_seed": null,
                "temperature": 0.1,
                "top_p": 0.1
            }
		}

the key is the name of the spec, that is passed in AGENTS_LLM_CONFIG_FILE_REF_KEY where as the agent information is passed in sub dicts planner_agent, browser_nav_agent, We prefer that this option should be ignored until you are sure what you are doing. discuss with us while playing around with these options in our discord communication.


The architecture:

testzeus-hercules system view
<screen shot of the architecture diagram>

Building on the foundation provided by the AutoGen agent framework, testzeus-hercules's architecture leverages the interplay between tools and agents. Each skill embodies an atomic action, a fundamental building block that, when executed, returns a natural language description of its outcome. This granularity allows testzeus-hercules to flexibly assemble these tools to tackle complex web automation workflows.

testzeus-hercules AutoGen setup

The diagram above shows the configuration chosen on top of AutoGen. The tools can be partitioned differently, but this is the one that we chose for the time being. We chose to use tools that map to what humans learn about the web browser rather than allow the LLM to write code as it pleases. We see the use of configured tools to be safer and more predictable in its outcomes. Certainly it can click on the wrong things, but at least it is not going to execute malicious unknown code.

Agents
At the moment there are two agents, the User proxy (executes the tools), and Browser navigation. Browser navigation agent embodies all the tools for interacting with the web browser.

tools Library
At the core of testzeus-hercules's capabilities is the tools Library, a repository of well-defined actions that the agent can perform; for now web actions. These tools are grouped into two main categories:

Sensing tools: tools like get_dom_with_content_type and geturl that help the agent understand the current state of the webpage or the browser.
Action tools: tools that allow the agent to interact with and manipulate the web environment, such as click, enter text, and open url.
Each skill is created with the intention to be as conversational as possible, making the interactions with LLMs more intuitive and error-tolerant. For instance, rather than simply returning a boolean value, a skill might explain in natural language what happened during its execution, enabling the LLM to better understand the context and correct course if necessary.

Below are the tools we have implemented:

Sensing tools	Action tools
geturl - Fetches and returns the current url.	click - given a DOM query selector, this will click on it.
get_dom_with_content_type - Retrieves the HTML DOM of the active page based on the specified content type. Content type can be:
- text_only: Extracts the inner text of the html DOM. Responds with text output.
- input_fields: Extracts the interactive elements in the DOM (button, input, textarea, etc.) and responds with a compact JSON object.
- all_fields: Extracts all the fields in the DOM and responds with a compact JSON object.	enter_text_and_click - Optimized method that combines enter text and click tools. The optimization here helps use cases such as enter text in a field and press the search button. Since the DOM would not have changed or changes should be immaterial to this action, identifying both selectors for an input field and an actionable button can happen based on the same DOM examination.
get_user_input - Provides the orchestrator with a mechanism to receive user feedback to disambiguate or seek clarity on fulfilling their request.	bulk_enter_text - Optimized method that wraps enter_text method so that multiple text entries can be performed one shot.
enter_text - Enters text in a field specified by the provided DOM query selector.
openurl - Opens the given URL in current or new tab.
DOM Distillation
testzeus-hercules's approach to managing the vast landscape of HTML DOM is methodical and, frankly, essential for efficiency. We've introduced DOM Distillation to pare down the DOM to just the elements pertinent to the user's task.

In practice, this means taking the expansive DOM and delivering a more digestible JSON snapshot. This isn't about just reducing size, it's about honing in on relevance, serving the LLMs only what's necessary to fulfill a request. So far we have three content types:

Text only: For when the mission is information retrieval, and the text is the target. No distractions.
Input fields: Zeroing in on elements that call for user interaction. It’s about streamlining actions.
All content: The full scope of distilled DOM, encompassing all elements when the task demands a comprehensive understanding.
It's a surgical procedure, carefully removing extraneous information while preserving the structure and content needed for the agent’s operation. Of course with any distillation there could be casualties, but the idea is to refine this over time to limit/eliminate them.

Since we can't rely on all web page authors to use best practices, such as adding unique ids to each HTML element, we had to inject our own attribute (mmid) in every DOM element. We can then guide the LLM to rely on using mmid in the generated DOM queries.

To cutdown on some of the DOM noise, we use the DOM Accessibility Tree rather than the regular HTML DOM. The accessibility tree by nature is geared towards helping screen readers, which is closer to the mission of web automation than plain old HTML DOM.

The distillation process is a work in progress. We look to refine this process and condense the DOM further aiming to make interactions faster, cost-effective, and more accurate.

Testing and Benchmarking
testzeus-hercules builds on the work done by Web Arena and Agent-E and beyond that to iron out the issues in the previous we have written are own testcases catering complex QA scenarios and have created tests in the ./tests folder.

Running Tests
To run the full test suite, use the following command:

make test

to run specific test


The agent is based on following papers published by different researchers

1. [Agent-E](https://arxiv.org/abs/2407.13032)
2. [Q Star](https://arxiv.org/abs/2312.10868)
3. [Agent Q](https://arxiv.org/abs/2408.07199)

and its infered and enhance over existing project of Agent-E (hyperlink: https://github.com/EmergenceAI/Agent-E), we have improved lots of cases to make it capable of doing testing, specially in the area of complex DOM navigation and iframes. We have also added new tools to the agent so that it can perform better work over the initial material we started with.


how to extend and attach tools

1) you can start extending by adding tools to the testzeus-hercules refer testzeus_hercules/core/tools/sql_calls.py as a an example on how to create a new tool, the key is decorator name tool over the method that you want testzeus-hercules to execute. the tool decorator should have very clear descirption and name so that the agent knows how to use the tool, also in the method you should be clear with annotation how what parameter is used for what purpose so that function calling in the LLM works best.
2) once you have created the new tools files in some folder path then you can pass the folder path to the agent in the env variable so that agent can read the new tools with extended one during the boot time and make sure that they are available during the execution. Use ADDITIONAL_TOOL_DIRS to pass the path of new tools folder where you have kept the new files.
3) in case if you opt for adding the tools directly then just put your new tools in the testzeus_hercules/core/tools path of the cloned reposiotry and then make sure you import your tool module in the "testzeus_hercules/core/agents/browser_nav_agent.py" file as from "testzeus_hercules.core.tools.sql_calls import *". This way is not recommended, we prefer you try to use the ADDITIONAL_TOOL_DIRS approach.


how to contribute back?
1)in case if you are developing tools for the agent and want to contribute to the community make sure you place the new tools in the additional_tools folder in your PR.
2) in case if you have a fix on sensing tools that are fundamental of the system or something in prompts or somehting in the DOM distillation code, then put the changes in the relevant file and share the PR.


Checking testzeus-hercules in action:
<video links for different cases 1>