# Troubleshooting

Start with `hercules doctor`. It checks your settings, the hardware tier and the model
server, and prints a `fix:` line for every problem it finds.

```bash
hercules doctor
hercules doctor --no-cache    # probe the server again after you changed it
```

## What the doctor checks mean

Each check is `ok`, `WARN`, `FAIL` or `skip` (not run because an earlier check failed).
`hercules doctor` exits with `0` when nothing failed, `1` when a check failed, and `2`
when the model server could not be reached.

| Check | What it tests | If it is not ok |
|---|---|---|
| `settings` | Config files and `HERCULES_*` variables are valid | The message names the file or variable and the bad key. Fix it and run again. |
| `memory` | Total memory and the tier it selects | `WARN` when the machine has less than 16 GB, when memory cannot be detected, or when `llm.tier` asks for more memory than the machine has |
| `endpoint` | The provider has a base URL and, if needed, a key | Set `llm.base_url` (or `HERCULES_LLM_BASE_URL`), or set the key variable the message names |
| `egress` | The egress policy allows this endpoint | `FAIL` for a remote server under policy `local`. Use a local server, or allow hosted models with `egress.policy: hybrid-text` (or the default, `hybrid`). The server is not contacted. |
| `reachable` | `GET /v1/models` answers | See [Model server not reachable](#model-server-not-reachable) |
| `server_version` | The server's version | `WARN` for Ollama older than 0.32.1: upgrade it for reliable tool calling |
| `model` | The configured model is served | The message lists the models the server has. On Ollama it prints the `ollama pull` command. Or set `llm.model` to a listed id. |
| `tool_calling` | The model can call tools | `FAIL`: Hercules cannot run. llama.cpp needs `--jinja`; vLLM needs `--enable-auto-tool-choice --tool-call-parser gemma4`; Ollama needs 0.32.1 or newer and a model with tool support. |
| `tool_choice` | The server honours a forced tool choice | `WARN` only: Hercules steers tool use through the prompt instead |
| `json_schema` | Structured output follows a JSON schema | `WARN` only: Hercules parses and repairs JSON output instead |
| `images` | The model reads images (a red/blue test image) | `WARN`: runs go without screenshots, and visual checks stay unverified in pure mode. Use mlx-vlm instead of mlx-lm, start llama.cpp with `--mmproj`, or use a vision-capable model build. |
| `context` | The server's context window is big enough for the tier | `FAIL` when it is too small, `WARN` when it cannot be read. See [Context window too small](#context-window-too-small). |
| `decider` | Who decides in a default run (`--mode auto`), and where the OpenRouter key comes from | `WARN` when no key is configured: runs are pure. Run `hercules setup` to add a key, or keep pure mode. `FAIL` when the settings contradict each other (for example `decider.backend: jev` under egress `local`). |
| `jev_key` | The key file `~/.hercules/openrouter.env` can be used | `WARN` when others can read the file (it is then ignored: `chmod 600 ~/.hercules/openrouter.env`) or it has no `OPENROUTER_API_KEY=` line |
| `clef` | A configured local Clef server answers one tiny decision | `WARN`: the local model decides instead. Start the server (see [Modes and privacy](modes-and-privacy.md#a-local-decision-model-clef)) or unset `decider.clef_url`. |

Results of the capability checks are cached in `~/.hercules/capabilities.json` and
reused by runs. Cached results are marked `(cached; run with --no-cache to re-probe)`.
They are kept apart for each `llm.request_params`, so changing the parameters probes again.

### A reasoning model answers nothing

Some hosted models (on any OpenAI-compatible endpoint) reason before they answer, and the
reasoning counts against the reply's token budget. The doctor's probes ask for only a few
tokens, so such a model can come back with no answer at all. The doctor then reports
`tool_calling`, `tool_choice`, `json_schema` or `images` as `WARN` with "not measured: the
model spent the probe's output on reasoning", instead of claiming the feature is missing,
and runs would get empty replies too. Tell the model to answer directly with
`llm.request_params` (or `HERCULES_LLM_REQUEST_PARAMS`), for example:

```bash
export HERCULES_LLM_REQUEST_PARAMS='{"reasoning_effort": "none"}'
hercules doctor --no-cache
```

The field's name and values depend on the endpoint; check its documentation.

## Model server not reachable

```text
FAIL  reachable  cannot reach http://127.0.0.1:11434/v1/models (ConnectError)
                 fix: start Ollama with `ollama serve` (or open the Ollama app)
```

- Start the server: `ollama serve`, `lms server start`, `llama-server ... --port 8080`,
  `vllm serve ... --port 8000`, or the mlx-vlm or mlx-lm server on port 8080.
- Check the provider and address: `hercules doctor` shows the endpoint it used. The base
  URL must end with the server's `/v1` path.
- `HTTP 401` or `403`: the server wants a key. Put it in an environment variable and set
  `llm.api_key_env` to that variable's name.
- "did not return an OpenAI-compatible model list": the URL points at something else,
  or misses `/v1`.

A run whose model server is down stops before its first step with an error and exit
code `2`. Every step is read, and every decision made, by a model: Hercules has no
rule-based reader or decider to fall back to, so start the server and check it with
`hercules doctor` (or configure one with `hercules setup`).

## Context window too small

```text
FAIL  context  effective context window is 4096 tokens (...); tier 'standard' needs at least 16384
```

Hercules needs at least 16,384 tokens, and more on bigger tiers (32,768 for
`recommended`, 65,536 for `comfortable`).

| Server | Fix |
|---|---|
| Ollama | `OLLAMA_CONTEXT_LENGTH=16384 ollama serve` (use your tier's value), or a Modelfile with `PARAMETER num_ctx 16384`. Check with `ollama ps`. |
| llama.cpp | `--ctx-size 16384`, multiplied by the `--parallel` value |
| vLLM | `--max-model-len 16384` |
| LM Studio | `lms load <model> --context-length 16384` |

Or pick a smaller tier with `llm.tier`.

## Slow runs and memory pressure

A local model needs most of the machine's memory. Signs of pressure: the first step
takes minutes, model replies time out, the system swaps, or the model server restarts.

- Run one case at a time. The daemon runs one at a time by default (two on the
  `comfortable` tier); don't raise `--parallel` on smaller machines, because parallel
  runs share one model server.
- Close other memory-heavy apps, and don't run two model servers at once.
- Use a smaller tier: `llm.tier: floor` uses Gemma 4 E4B. On Ollama, the QAT build of
  26B-A4B (`gemma4:26b-a4b-it-qat`) needs less memory than Q4_K_M.
- Check what the server has loaded, for example with `ollama ps`, and that it is not
  holding other models.
- Raise the reply timeout if a slow machine hits it: `llm.request_timeout_s` (default
  300) or `HERCULES_LLM_TIMEOUT_S`.
- Use hybrid mode if your policy allows it: the hosted decision model takes the
  decisions off the local model. `hercules setup` adds the key
  ([Modes and privacy](modes-and-privacy.md)).

## Hybrid mode problems

| Symptom | Cause and fix |
|---|---|
| `notice: hybrid mode (JEV decides) needs an OpenRouter key and none is configured` | No key in `OPENROUTER_API_KEY` or `~/.hercules/openrouter.env`. Run `hercules setup`, or ignore it: the run is pure. |
| The notice says the key file is readable by other users | Hercules ignores a key file others can read. Run `chmod 600 ~/.hercules/openrouter.env`. |
| `JEV authentication failed (HTTP 401)` | The key is wrong or revoked. Run `hercules setup` again; it checks the key before saving it. |
| `JEV account cannot pay for requests (HTTP 402)` | Add credits to the OpenRouter account. Decisions fall back to the local model meanwhile. |
| `JEV rejected ... (HTTP 400)` or `404` mentioning providers | OpenRouter could not route the request with zero data retention. Turn the request off with `decider.zdr: false` (or `HERCULES_DECIDER_ZDR=0`) and report it. |
| Many JEV calls for one step | The page lists more than 32 KB of candidate text (or more than 254 elements), so the decision is split into parts and a final pick (see [Modes and privacy](modes-and-privacy.md#hybrid-mode-the-default)). Every element is offered; nothing is trimmed by matching words. |
| `hybrid mode sends page text to JEV ... and the egress policy is 'local'` | `--mode hybrid` under egress `local`. Use `--egress hybrid-text` or `hybrid`, or `--mode pure`. |

After each run, `hercules run` prints the number of local model calls, their total and
mean time, the slowest call, and the model warm-up time. Use these to see where time
goes.

## Browser not found

```text
Hint: the browser could not start (Error): BrowserType.launch: Executable doesn't exist at ...
Set HERCULES_CHROMIUM to a Chromium executable or run `playwright install chromium`.
```

Install the Chromium build for the Playwright version in the same environment as
Hercules:

```bash
python -m playwright install chromium
```

(with uv in a clone: `uv run python -m playwright install chromium`). Or point
`HERCULES_CHROMIUM` at a Chromium executable you already have. On Linux, Playwright may
also need system libraries: `python -m playwright install --with-deps chromium`.

A case with `"browser": "firefox"`, `"webkit"`, `"chrome"` or `"msedge"` needs that browser:
`python -m playwright install firefox` (or `webkit`, `chrome`, `msedge`). The run's error names
the command. A problem with `browser_options` (a missing secret variable, an auth file that is
not in the project) stops the run before the browser starts and names what is missing; see
[Browsers and browser options](browsers.md).

## The page could not be read

```text
step 3 error: the page could not be read: TypeError: Array.prototype.includes is disabled in this sandbox
```

Hercules reads a page by running a small script (the walker) in it that lists the page's
controls. Some apps replace or lock down the browser's built-in functions and prototypes,
or define custom elements whose properties throw when read. The walker is written to survive this: an element that cannot
be read is skipped, and the rest of the page is still listed. When the walker cannot run
at all, Hercules waits for the page to settle and tries once more, then (in Chromium)
reads the page again in an isolated world where the page's own scripts are not present.
Only when all of that fails does the step end as `error` with the message above, which is
the page's own JavaScript error. The run's `failure.category` is then `environment`: the
test and the product were not judged.

What to look at in the step's evidence:

- `observation.txt` starts with `# page: ... · the page could not be read`, then
  `# read error: ...` and one `# attempt N: ...` line per try.
- `perception_error.json` holds the same error, the page's address, each attempt and the
  element errors the walker counted.
- `plain.jpg` shows what the page looked like.

Pages that could be read but not completely say so in the observation header:

| Header line | Meaning |
|---|---|
| `# read: 12 elements skipped (first error: ...)` | Those elements threw when read and are not in the list a model chooses from. |
| `# read: frames not read: <name> (...)` | A frame of the page could not be walked; the rest of the page was. |
| `# read: read in an isolated world: ...` | The page's scripts broke the walker, and the page was read without them. Its frames are not read that way. |

If a step needs an element that is skipped, report the page's error message (it names
what the page refused) with the step's evidence.

## A step picks nothing while a dialog is open

When a modal dialog is open, only its elements are offered: the observation header says
`# modal: "<dialog name>" open; N elements behind it not offered`. A step that names
something behind the dialog (a button of the list page under it) finds nothing until the
dialog is closed: add a step that saves, cancels or closes the dialog first. Elements in an
`inert` or `aria-hidden="true"` part of the page are not offered either (`# hidden: ...`).
If the header names a dialog that is not on screen, the page left an `aria-modal` element
visible; when that dialog holds nothing that can be read, every element is offered and the
header says so.

A field that a dialog re-renders (after a scroll, or on a timer) is found again by its role
and name inside the dialog, or in a fresh read of the page; `plan.jsonl` shows a
`"stage": "resolve"` record when that happened. `TargetGone` means none of these found
exactly one element with the field's role and name.

## A field rejects a value, or a step runs in another order

- **`the "<field>" field rejected "<value>": <error>`**: the field marked the value invalid
  once it lost focus and no rewrite in the field's format was accepted. Look at the step's
  `plan.jsonl` (`stage: field`): the model's proposal, why it was dropped (`dropped`), and
  the `meaning` decision in `decisions.jsonl`. Write the value in the format the app
  expects, or, for a negative test, state the expected error in the step or the next step.
- **`entered "31/12/2027" for "12/31/2027" … to match the field's format`** in the hints:
  the step passed with a rewritten value. Update the test's data if you want it to run as
  written.
- **`the field accepts at most N characters`**, classified `test_data`: the test's value is
  longer than the field allows. Hercules never shortens it; fix the value.
- **`the page marks the field invalid at the end of the step`**: the field was accepted when
  it was entered but the page flagged it later in the same step (a delayed check, or a field
  another action of the step invalidated).
- **A step reports `sequence` or a note like `run before step 20 because the "State" field
  depends on it`**: the case reading (or the run, when a field was disabled or empty) found
  that a field depends on one entered later, and set that one first. The step's
  `plan.jsonl` has the `order` and `depends` records. Writing dependent fields in their
  natural order avoids it.
- **Fills are slower than before**: each fill now waits briefly after leaving the field for
  the page to validate it (about 0.2 s, up to about 1.5 s when the page sends a validation
  request).

## Daemon, token and port problems

| Symptom | Cause and fix |
|---|---|
| `hercules serve: a Hercules daemon is already running (pid ..., http://127.0.0.1:7373)` | One daemon runs per Hercules home folder. Use it, or stop it (Ctrl+C in its terminal, or end that process) and start again. |
| `port 7373 on 127.0.0.1 is in use` | Another program uses the port. Pass `--port` with another port, `--port 0` for any free port, or `--fallback-port`. |
| `refusing to bind '0.0.0.0'` | The daemon listens on loopback only. To expose it, use `--allow-remote` and set `HERCULES_DAEMON_TOKEN` to a random value of at least 32 characters. |
| `hercules ui: no Hercules daemon is running` | Start `hercules serve` in your project first. |
| The sign-in link says it is expired or used | Links work once and expire after 5 minutes. Run `hercules ui` for a new one. |
| `401 unauthorized` from the API | Send `Authorization: Bearer <token>` with the token from `~/.hercules/daemon.json`. The token changes every time the daemon starts, unless `HERCULES_DAEMON_TOKEN` is set. |
| `403 forbidden host` or `forbidden origin` | Requests must use a loopback address (`127.0.0.1`, `localhost`, `::1`) and come from a loopback page. Open the UI through the link `hercules ui` prints. |
| A coding agent's tools say the daemon "did not become ready" | The daemon failed to start within 8 seconds. Read `~/.hercules/daemon.log`, or start `hercules serve` yourself to see the error. |
| Runs use the wrong project | The running daemon serves another project. `hercules_check_env` (or `GET /api/health`) shows which. Stop it and start `hercules serve` in the right project. |
| A secret or the hybrid-mode key is "not set" in daemon runs | The daemon reads variables from the environment it was started in. Restart `hercules serve` in a shell where they are set, or keep the key in `~/.hercules/openrouter.env` (`hercules setup`), which daemon runs read too. |
| A run stays `error` after a crash | If the run page shows **Resume run**, resume it from the last completed step. Otherwise read `worker.log` in the run folder. |

## Case errors

| Message | Fix |
|---|---|
| `invalid test case in <file>: ...` | The message names each bad field. Unknown fields are rejected, so check spelling. |
| `unknown placeholder(s) {{name}} ...; known names: ...` | Add `name` to `test_data`, or fix the spelling |
| `secret 'password' needs the environment variable APP_TEST_PASSWORD, which is not set` | Export the variable before the run (or before `hercules serve`) |
| `relative URL '/login' needs the case's environment.base_url` | Set `environment.base_url`, or pass `--base-url` |
| `<file>:<line>: Scenario Outline / Examples are not supported yet` | Write one scenario per file, or convert it with `hercules import v1` |
| `expected exactly one scenario, found N` | Put each scenario in its own `.feature` file |
| A check is `unverified` | A model judged it (`semantic: true`, an outcome with no exact text, or one about appearance) and was not confident either way, or it is about appearance and no engine could see the screenshot. Quote the exact text you expect, or let images reach the decider (`--egress hybrid`, or a local model with image support). |
| `precondition not met: '...'` | A model judged on the page as the run started that a precondition does not hold; no step ran. Check the page in `preconditions/plain.jpg`, fix the state, or reword the precondition. |
| `model reading of case part 2 of 4 (steps 17-32) failed (LLMRequestError, HTTP 400)` in `worker.log` | A long case is read in parts; that part was refused by the model endpoint (a 400 often means the reply schema or output was too large for it). It was tried once more, then only its steps were read one at a time on the page, so the run still goes on. Other parts are unaffected. |
| A step's `plan.jsonl` shows a `reread` stage | The case reading did not work on the page (the element it named was not found, or a value was missing), so the model read the step again with the page's controls. |
