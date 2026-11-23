# Getting started

> **Watch it:** [One line to install and set up (1:19)](videos.md#1-one-line-to-install-and-set-up).

Hercules runs end-to-end tests of a web app in a real browser on your machine. The local
model (a chat model on a server on your machine, or any OpenAI-compatible endpoint)
reads your test steps. With an OpenRouter key, JEV, a hosted decision model, makes the
decisions (which element, did the step work); without one, the local model makes them
too. With a model server on your machine and the egress policy `local`, nothing leaves
your machine. See [Modes and privacy](modes-and-privacy.md).

This page takes you from install to your first run.

**The quickest start** is one line, which installs Hercules and the browsers you choose and runs
[`hercules setup`](#guided-setup-hercules-setup), asking before each change:

```bash
uvx testzeus-hercules
```

Or paste the [coding-agent setup prompt](agent-setup.md) into your coding agent and let it
ask you the questions. The rest of this page does the same steps by hand.

## What you need

- macOS or Linux. To run the model on your machine, at least 16 GB of memory (see
  [Hardware tiers](#hardware-tiers)).
- Python 3.13 or newer. The package accepts Python 3.13 to 3.15; 3.13 is the version we recommend.
- A model endpoint that speaks the OpenAI chat-completions API: a local server such as
  Ollama, mlx-vlm, mlx-lm, LM Studio, llama.cpp or vLLM, or a hosted API or LiteLLM proxy
  (see [Use any OpenAI-compatible endpoint](#use-any-openai-compatible-endpoint)).
- Chromium for Playwright (installed below).
- Optional: an OpenRouter API key with access to JEV, for hybrid mode (the default when a
  key is configured).

## Install

v2 is not yet released on PyPI: `pip install testzeus-hercules` still installs v1. Until
the release, install from a clone of the repository:

```bash
git clone https://github.com/test-zeus-ai/testzeus-hercules.git
cd testzeus-hercules
uv sync --python 3.13
uv run python -m playwright install chromium
source .venv/bin/activate
```

With the virtual environment active, `hercules` works in any folder, so you can run it
in your app's project. Without uv:

```bash
git clone https://github.com/test-zeus-ai/testzeus-hercules.git
cd testzeus-hercules
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

Once v2 is on PyPI, `uv pip install testzeus-hercules` (or `pip install`) in a Python 3.13
virtual environment, followed by `python -m playwright install chromium`, does the same.

Check the install:

```bash
hercules version
hercules --help
```

To use a Chromium you already have instead of Playwright's build, set
`HERCULES_CHROMIUM` to the path of its executable.

## Hardware tiers

Hercules picks a hardware tier from your total system memory. The tier sets the default
local model, the context window the model server must allow, and how much of each page
is sent to the model. On Apple Silicon, system memory is the unified memory the GPU uses.
On machines with a discrete GPU, Hercules does not read VRAM: set the tier yourself
(`llm.tier`, see [Configuration](#configuration)).

| Tier | Memory | Default model | Context the server must allow | Vision budget | Parallel runs |
|---|---|---|---|---|---|
| `floor` | 16 GB | Gemma 4 E4B (`gemma-4-e4b`) | 16,384 tokens | 280 tokens | 1 |
| `standard` | 24 GB | Gemma 4 26B-A4B (`gemma-4-26b-a4b`) | 16,384 tokens | 280 tokens | 1 |
| `recommended` | 32 GB | Gemma 4 26B-A4B | 32,768 tokens | 280 tokens | 1 |
| `comfortable` | 48 GB | Gemma 4 26B-A4B | 65,536 tokens | 560 tokens | 2 |

- 16 GB is the minimum. On a machine with less, Hercules still starts with the `floor`
  settings and `hercules doctor` warns that local models may be slow or fail.
- Operating systems report a little less than the nominal memory, so a "16 GB" machine
  that shows about 15 GiB still lands on the 16 GB tier.
- Setting `llm.tier` explicitly always wins. If the tier needs more memory than the
  machine has, `hercules doctor` warns you.

Gemma 4 26B-A4B is the default model from the `standard` tier up. The `floor` tier uses
Gemma 4 E4B, which fits in 16 GB.

## Set up a local model server

Hercules sends every model request to an OpenAI-compatible `/v1` endpoint. Pick one of
the named providers below; each has a default address, so usually you only set the
provider.

| Provider (`llm.provider`) | Default base URL | Notes |
|---|---|---|
| `ollama` (default) | `http://127.0.0.1:11434/v1` | Needs Ollama 0.32.1 or newer. Images are sent as data URLs. |
| `mlx-vlm` | `http://127.0.0.1:8080/v1` | Best option on a Mac: text, images, tool calling, JSON schema. |
| `mlx` | `http://127.0.0.1:8080/v1` | The mlx-lm server. Text only, no JSON schema output. |
| `lmstudio` | `http://127.0.0.1:1234/v1` | LM Studio's local server. |
| `llamacpp` | `http://127.0.0.1:8080/v1` | `llama-server` from llama.cpp. |
| `vllm` | `http://127.0.0.1:8000/v1` | vLLM (Linux with a GPU). |
| `openai-compatible` | none: set `llm.base_url` | Any other OpenAI-compatible server or hosted endpoint (see [below](#use-any-openai-compatible-endpoint)). |
| `litellm` | `http://127.0.0.1:4000/v1` | A LiteLLM proxy. Key optional (`LITELLM_API_KEY`). |

You configure the model by its canonical name (`gemma-4-26b-a4b` or `gemma-4-e4b`), or
leave `llm.model` at `auto` to use the tier's model. Hercules asks the server for its
model list and finds the matching id for that server, for example:

| Server | Ids Hercules looks for (26B-A4B) |
|---|---|
| Ollama | `gemma4:26b-a4b-it-qat`, `gemma4:26b-a4b-it-q4_K_M` |
| LM Studio | `google/gemma-4-26b-a4b` |
| vLLM | `google/gemma-4-26B-A4B-it` |
| mlx-vlm, mlx-lm | `mlx-community/gemma-4-26B-A4B-it-4bit` |

On Ollama, the `standard` tier prefers the QAT build and the `recommended` and
`comfortable` tiers prefer the Q4_K_M build. For the E4B model on Ollama, Hercules
looks for `gemma4:e4b`. You can also set `llm.model` to any id your server lists.

### Ollama

```bash
ollama pull gemma4:26b-a4b-it-qat
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

Ollama's default context window is smaller than Hercules needs, and it cannot be raised
through the OpenAI API. Start the server with `OLLAMA_CONTEXT_LENGTH` set to at least
the value for your tier (32768 for `recommended`, 65536 for `comfortable`), or build the
model from a Modelfile with `PARAMETER num_ctx`. Check the loaded context with
`ollama ps`.

### mlx-vlm and mlx-lm (Mac)

Start the mlx-vlm server with `mlx-community/gemma-4-26B-A4B-it-4bit` on port 8080 and
set the provider:

```bash
export HERCULES_LLM_PROVIDER=mlx-vlm
```

mlx-lm also works but is text only, so Hercules runs without screenshots:

```bash
mlx_lm.server --model mlx-community/gemma-4-26B-A4B-it-4bit --port 8080
export HERCULES_LLM_PROVIDER=mlx
```

### LM Studio

```bash
lms server start
lms load google/gemma-4-26b-a4b --context-length 32768
export HERCULES_LLM_PROVIDER=lmstudio
```

### llama.cpp

Start `llama-server` with `--jinja` (needed for tool calls) and `--mmproj` (needed for
images):

```bash
llama-server -m <model.gguf> --mmproj <vision-projector.gguf> --jinja --ctx-size 32768 --port 8080
export HERCULES_LLM_PROVIDER=llamacpp
```

With `--parallel`, the context size is shared between slots: multiply it by the number
of slots.

### vLLM

```bash
vllm serve google/gemma-4-26B-A4B-it --port 8000 --max-model-len 32768 \
  --enable-auto-tool-choice --tool-call-parser gemma4
export HERCULES_LLM_PROVIDER=vllm
```

### Use any OpenAI-compatible endpoint

The model role works with any endpoint that speaks the OpenAI chat-completions API: another
local server, a proxy such as LiteLLM in front of many models, or a hosted service.

```bash
export HERCULES_LLM_PROVIDER=openai-compatible
export HERCULES_LLM_BASE_URL=http://127.0.0.1:9000/v1
export HERCULES_LLM_MODEL=<model name the endpoint uses>
```

- **Model names** are passed through exactly as written, slashes included
  (`gemini/gemini-2.5-flash`, `bedrock/anthropic.claude-sonnet`, `openai/gpt-4o`). Only the
  canonical names (`gemma-4-26b-a4b`, `gemma-4-e4b`) are looked up in the model list.
- **Keys** are optional. If the endpoint needs one, put it in an environment variable and
  name that variable in `HERCULES_LLM_API_KEY_ENV` (or `llm.api_key_env`). Hercules never
  stores a key in its configuration.
- **Extra headers** for a gateway go in `llm.headers` (or `HERCULES_LLM_HEADERS` as JSON):
  header name to the *name* of the environment variable that holds its value, for example
  `{"x-api-key": "GATEWAY_KEY"}`. A value written there is refused, and header values are
  never printed.
- **Extra request fields** go in `llm.request_params` (or `HERCULES_LLM_REQUEST_PARAMS` as
  JSON), and are sent with every request. A reasoning model, for example, may spend its
  whole answer budget on reasoning and return nothing unless it is told to answer directly:
  `{"reasoning_effort": "none"}` on endpoints that support it. Hercules' own request fields
  (`model`, `messages`, `stream`, `response_format`, `tools`, `tool_choice`) cannot be set
  this way, and keys are refused.
- **Capabilities** (tool calling, JSON schema output, images) are never assumed for these
  endpoints: run `hercules doctor` to measure them. Runs use what it measured.
- **Local or remote** is decided from the URL (see below). A remote endpoint needs the
  egress policy `hybrid-text` or `hybrid` (the default), and step text and page content are
  sent to it; screenshots too when images are on. See
  [Modes and privacy](modes-and-privacy.md#egress-policy).

**A LiteLLM proxy** has its own provider name, with the proxy's default address:

```bash
litellm --config litellm.yaml                      # the proxy, on http://127.0.0.1:4000
export HERCULES_LLM_PROVIDER=litellm
export HERCULES_LLM_MODEL=gemini/gemini-2.5-flash  # a model_name from the proxy's config
# export LITELLM_API_KEY=...                       # only if the proxy has a master key
```

A LiteLLM proxy may answer from its response cache, which makes a run look much faster
than the model is. With `litellm` Hercules asks for a fresh answer on every request
(`llm.cache: bypass`, the default for this provider; set `llm.cache: allow` or
`HERCULES_LLM_CACHE=allow` to let the cache answer). Answers that still come from the
cache are counted in the run's summary and in `engines.llm_cache_hits` in `run.json`, and
`hercules doctor` warns when its probes were answered from a cache.

**A hosted endpoint**, for example Cerebras:

```bash
export HERCULES_LLM_PROVIDER=openai-compatible
export HERCULES_LLM_BASE_URL=https://api.cerebras.ai/v1
export HERCULES_LLM_MODEL=<model name the endpoint lists>
export HERCULES_LLM_API_KEY_ENV=CEREBRAS_API_KEY        # the variable that holds the key
export HERCULES_LLM_REQUEST_PARAMS='{"reasoning_effort": "none"}'
hercules doctor --no-cache
```

Only a server on this machine (`127.0.0.1`, `::1` or `localhost`) counts as local. A
server on another machine, even in your private network (a `10.x` or `192.168.x`
address, or a `.local` name), is remote and the egress policy `local` blocks it, unless
you list that machine in `egress.local_hosts` (see
[Modes and privacy](modes-and-privacy.md)).

## Guided setup: `hercules setup`

```bash
hercules setup
```

`hercules setup` starts and downloads nothing. It walks through five steps and saves
the first four in `~/.hercules/config.yaml`:

1. **Model.** It asks each local server's usual address for its model list and marks the
   server that has your hardware tier's recommended model. Instead of a local server you
   can pick a hosted model:
   - `o`, **OpenRouter**: the model runs on OpenRouter with your OpenRouter key, the same
     key as step 2 (which then also turns on hybrid mode). No local server is needed.
   - `u`, **another OpenAI-compatible endpoint** (OpenAI, Groq, Together, a LiteLLM proxy,
     a server elsewhere on your network): it asks for the URL and the key (hidden), checks
     them with one `GET /models` request, and lists the endpoint's models to pick from. The
     key is saved in `~/.hercules/llm.env`, readable only by you; the configuration holds
     only the name of the variable (`HERCULES_LLM_API_KEY` unless you name another).

   Runs, `hercules doctor` and the daemon read the key from there, so the tester UI opens
   with the model ready.
2. **OpenRouter key** for hybrid mode. It finds the key in `OPENROUTER_API_KEY` or in
   `~/.hercules/openrouter.env`, or asks you to paste it (the input is hidden) and saves
   it in that file, readable only by you. One tiny JEV request checks that the key works.
   Leave it empty to stay in pure mode.
3. **Egress policy**: `hybrid` (default), `hybrid-text` or `local`.
4. **Telemetry**: it shows what is sent and asks whether to keep it on.
5. **Coding agents**: it lists the coding agents it finds, by their command on your
   `PATH` (`claude`, `codex`, `cursor`, `code`, `gemini`) or their files in the project
   (`.claude/`, `AGENTS.md`, `.cursor/`, `.vscode/`, `.gemini/`). For each one not yet
   connected it asks before adding the Hercules skill and MCP server to the project (the
   same files as [`hercules init`](coding-agents.md)), so the agent tests its changes
   through Hercules. Nothing is written without a yes, and existing files are never
   replaced. Run `hercules setup` inside your project for this step.

For CI, answer every question with its default and pass single answers as flags:

```bash
echo "$OPENROUTER_API_KEY" | hercules setup --yes --key-stdin --provider ollama --telemetry off
hercules setup --yes --egress local          # no key, nothing leaves the machine
hercules setup --yes --provider openrouter --key-stdin < key.txt
echo "$GROQ_API_KEY" | hercules setup --yes --provider openai-compatible \
  --base-url https://api.groq.com/openai/v1 --model llama-3.3-70b-versatile --llm-key-stdin
```

| Option | Meaning |
|---|---|
| `--yes` | Non-interactive: use the detected server, the key from the environment (or `--key-stdin`), egress `hybrid` with a key, telemetry on; no coding agent is connected unless `--agents` names it |
| `--provider`, `--base-url`, `--model` | The model, instead of detecting a local server: a local provider (`ollama`, `mlx-vlm`, `llamacpp`, `lmstudio`, `vllm`), `openrouter`, or `openai-compatible` with `--base-url` and the endpoint's `--model` id |
| `--api-key-env NAME` | The variable that holds a hosted endpoint's key (default: the provider's, or `HERCULES_LLM_API_KEY`) |
| `--llm-key-stdin` | Read the hosted endpoint's key from the first line of standard input, check it and save it to `~/.hercules/llm.env` |
| `--key-stdin` | Read the key from the first line of standard input and save it |
| `--save-key` | Save a key found in the environment to `~/.hercules/openrouter.env` |
| `--no-key-check` | Skip the JEV request that checks the key |
| `--agents LIST` | Connect these coding agents without asking: comma-separated `claude`, `codex`, `cursor`, `copilot`, `gemini`, `generic`, or `none`. With `--yes`, no agent is connected unless named here |
| `--egress POLICY` | `hybrid`, `hybrid-text` or `local` |
| `--telemetry on\|off` | Anonymous usage telemetry |
| `--clef-url URL` | A local Clef server that decides in pure mode |
| `--scope project` | Write the project's `.hercules/config.yaml` instead |

A hosted model sends page text (and screenshots, under `hybrid`) to that endpoint, so it
needs egress `hybrid` or `hybrid-text`; setup picks `hybrid` for it and refuses `local`.

Exit codes: `0` done, `1` the key check failed (the key is not saved), `2` bad input.

## Configuration

Hercules reads settings from these places, later ones winning:

1. Built-in defaults.
2. `~/.hercules/config.yaml` (your user settings; the folder moves with `HERCULES_HOME`).
3. `<project>/.hercules/config.yaml` (project settings).
4. `HERCULES_*` environment variables.
5. Command-line flags.

Example `.hercules/config.yaml`:

```yaml
llm:
  provider: ollama
  model: auto        # or gemma-4-26b-a4b, gemma-4-e4b, or a server id
  tier: auto         # floor, standard, recommended, comfortable
  request_timeout_s: 300
egress:
  policy: hybrid     # hybrid, hybrid-text, local
telemetry:
  enabled: true
```

| Setting | Environment variable | Default | Meaning |
|---|---|---|---|
| `llm.provider` | `HERCULES_LLM_PROVIDER` | `ollama` | Model server type (table above) |
| `llm.model` | `HERCULES_LLM_MODEL` | `auto` | Canonical model name, server id, or `auto` (the tier's model) |
| `llm.base_url` | `HERCULES_LLM_BASE_URL` | the provider's default | The server's `/v1` URL |
| `llm.api_key_env` | `HERCULES_LLM_API_KEY_ENV` | none | Name of the variable that holds the server's key |
| `llm.tier` | `HERCULES_LLM_TIER` | `auto` | Hardware tier |
| `llm.request_timeout_s` | `HERCULES_LLM_TIMEOUT_S` | `300` | Seconds to wait for one model reply |
| `llm.request_params` | `HERCULES_LLM_REQUEST_PARAMS` (JSON object) | none | Extra fields sent with every request to the model server, for example `{"reasoning_effort": "none"}` |
| `llm.headers` | `HERCULES_LLM_HEADERS` (JSON object) | none | Extra request headers: header name to the name of the variable that holds its value |
| `llm.cache` | `HERCULES_LLM_CACHE` | `bypass` for `litellm`, else `allow` | Whether a caching proxy may answer from its cache |
| `llm.max_retries` | none | `1` | Retries for connection errors, 429 and 5xx |
| `decider.backend` | `HERCULES_DECIDER_BACKEND` | unset | `local` (pure mode), `jev` (hybrid mode) or `clef` (pure mode, a local Clef server decides); unset picks per run |
| `decider.model` | none | unset | Decision model id for hybrid mode |
| `decider.api_key_env` | none | `OPENROUTER_API_KEY` | Variable that holds the hybrid-mode key (else `~/.hercules/openrouter.env`) |
| `decider.zdr` | `HERCULES_DECIDER_ZDR` | `true` | Ask OpenRouter for zero data retention on JEV requests |
| `decider.max_candidates` | none | `254` | No longer used (every element is offered to the decider); accepted so existing configs load |
| `decider.clef_url` | `HERCULES_CLEF_URL` | unset | A local Clef server that decides in pure mode |
| `decider.clef_model` | `HERCULES_CLEF_MODEL` | `Cloudflare/clef-flash` | The model id the Clef server expects |
| `egress.policy` | `HERCULES_EGRESS` | `hybrid` | What may leave the machine |
| `egress.local_hosts` | none | none | Other machines (host names or IP addresses) whose model or Clef server counts as local |
| `telemetry.enabled` | `HERCULES_TELEMETRY` | `true` | Anonymous usage telemetry (always off under egress `local`; `DO_NOT_TRACK=1` or `--no-telemetry` also turn it off) |
| `ui.host` | `HERCULES_UI_HOST` | `127.0.0.1` | Address the daemon listens on |
| `ui.port` | `HERCULES_UI_PORT` | `7373` | Port the daemon listens on |
| `ui.notifications.runs` | none | `all` | Notify when a run ends: `all`, `failures` or `off` ([Notifications](running-tests.md#notifications)) |
| `ui.notifications.approvals` | none | `true` | Notify when a run waits for an approval |
| `ui.notifications.explore` | none | `true` | Notify when an Explore session ends |
| `ui.notifications.sound` | none | `true` | A chime with each notification |
| `ui.notifications.desktop` | none | `true` | System notifications while the UI is in the background |

Other environment variables:

| Variable | Meaning |
|---|---|
| `HERCULES_HOME` | Hercules home folder (default `~/.hercules`) |
| `HERCULES_CHROMIUM` | Path of a Chromium executable to use instead of Playwright's |
| `HERCULES_DAEMON_TOKEN` | API token for a daemon exposed with `--allow-remote` |
| `DO_NOT_TRACK` | `1` turns telemetry off, unless `HERCULES_TELEMETRY` is set |
| `BROWSERSTACK_USERNAME`, `BROWSERSTACK_ACCESS_KEY`, `SAUCE_USERNAME`, `SAUCE_ACCESS_KEY`, `LT_USERNAME`, `LT_ACCESS_KEY` | Test cloud credentials ([Running tests on clouds](clouds.md)); else `~/.hercules/clouds.env` |

Every command also accepts `--no-telemetry`, before or after the command name
(`hercules --no-telemetry run case.json`). It turns telemetry off for that command and
for any daemon or worker it starts.

Configuration files reject keys that look like secrets (`api_key`, `token`, `password`
and similar). Keep secrets in environment variables and name the variable instead.

## Check your setup with `hercules doctor`

```bash
hercules doctor
```

`hercules doctor` shows the effective settings, the hardware tier, who makes the
decisions in a default run, and the result of each check against your model server:

```text
Hercules doctor (<version>)

Settings
  provider     ollama
  model        auto -> gemma-4-26b-a4b
  endpoint     http://127.0.0.1:11434/v1 (local)
  egress       hybrid
  api key env  -
  telemetry    on
  decider      hybrid: JEV typesafe/jev-1.14 via OpenRouter (zero data retention requested; sends page text and, when JEV is unsure, screenshots) [auto: OPENROUTER_API_KEY is set and egress policy is 'hybrid']
  tier         standard (auto, 24.0 GiB memory)
  budgets      context 16384 tokens, observation 120 elements / 2500 tokens, vision 280 tokens, 1 parallel run(s)

Checks
  ok    memory          24.0 GiB -> tier standard (auto)
  ok    egress          policy 'hybrid' allows this local endpoint
  FAIL  reachable       cannot reach http://127.0.0.1:11434/v1/models (ConnectError)
                        fix: start Ollama with `ollama serve` (or open the Ollama app)
  skip  server_version  server not reachable
  ...
Result: FAIL (exit 2)
```

Each failing or warning check prints a `fix:` line. Fix the first failure and run
`hercules doctor` again.

| Option | Meaning |
|---|---|
| `--json` | Print the report as JSON |
| `--project DIR` | Project directory (default: the current directory) |
| `--no-cache` | Probe the server again instead of reusing cached capabilities |

Exit codes: `0` when every check is ok or a warning, `1` when a check fails, `2` when the
model server cannot be reached.

`hercules doctor` saves what it learned about the server (tool calling, JSON schema
output, images, context) in `~/.hercules/capabilities.json`. Runs reuse it. After you
change the server, model or its settings, run `hercules doctor --no-cache`.

See [Troubleshooting](troubleshooting.md#what-the-doctor-checks-mean) for what each
check means.

## Your first run

1. Start the app you want to test, for example on `http://localhost:3000`.
2. Create a project folder (any folder; Hercules keeps its files in `.hercules/` inside it)
   and save this case as `.hercules/cases/sign-in.json`:

```json
{
  "id": "sign-in",
  "title": "User signs in",
  "environment": {"base_url": "http://localhost:3000"},
  "test_data": {
    "email": "demo@example.com",
    "password": {"secret": "APP_TEST_PASSWORD"}
  },
  "steps": [
    {"action": "Open \"/login\""},
    {"action": "Fill \"{{email}}\" in \"Email\""},
    {"action": "Fill \"{{password}}\" in \"Password\""},
    {"action": "Click the \"Sign in\" button", "expected": "The text \"Welcome back\" is shown"}
  ]
}
```

3. Set the secret and run the case:

```bash
export APP_TEST_PASSWORD=...
hercules run .hercules/cases/sign-in.json
```

Hercules prints the mode it chose, then a summary:

```text
Mode: hybrid [auto: OPENROUTER_API_KEY is read from ~/.hercules/openrouter.env and egress policy is 'hybrid'] - JEV typesafe/jev-1.14 decides (text and screenshots when unsure); local model gemma4:26b-a4b-it-qat via ollama at http://127.0.0.1:11434/v1, tier standard interprets and covers JEV outages; zero data retention requested
Run r_20261026_101500_ab12cd PASSED (case sign-in, 12.4s, verdict: ledger)
Steps: 4 passed of 4 reported
Assertions: 1/1 passed, 0 failed, 0 unverified
...
Decisions: jev-openrouter 4 (step 2: jev-openrouter; ...)
JEV calls: 4, cost $0.0003
Local model calls: 5, 9.8 s total, ...
Evidence: /path/to/project/.hercules/runs/r_20261026_101500_ab12cd
```

Without a key the run is pure, and one `notice:` line before the mode line says so.

The evidence folder holds the screenshots and the result of every step. Add `--headed`
to watch the browser.

Every run needs the local model server: the model reads every step (the whole case in
one call before the first step, cached for the next run of the same case) and, in pure
mode, makes every decision. There is no model-free mode; if the server is down,
`hercules run` exits with code 2 and points you to `hercules doctor`.

## Next steps

- [Writing tests](writing-tests.md): the case format, test data, secrets and Gherkin.
- [Running tests](running-tests.md): all run options, the daemon and the tester UI.
- [Coding agents](coding-agents.md): use Hercules from Claude Code, Codex or Cursor.
- [Architecture](architecture.md): how a run works inside.
