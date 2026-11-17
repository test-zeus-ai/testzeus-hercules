# Modes and privacy

Hercules runs the browser, your test cases and the evidence on your machine, and usually
the local model too. When an OpenRouter key is configured, it also asks JEV, a hosted
decision model, to make the decisions (which element, which tool, did the step work).
This page explains what leaves the machine, when, and how to keep everything local.

The quickest way to set this up is `hercules setup` (see
[Getting started](getting-started.md)).

## Run modes

Every run uses one of two modes. In both, every step is read and every decision is made
by a model; Hercules has no rule-based step reader and no coded decider to fall back to.

### Hybrid mode (the default)

The work is split between the local model and JEV, served through OpenRouter:

| Task | Who does it |
|---|---|
| Reading steps and splitting them into actions (the whole case in one call before the run, cached), and every other language task (for example drafting cases from a document) | Local model |
| Picking the tool and the element, judging whether a step worked | JEV |
| Judging an expected outcome by meaning (`semantic: true`), a case's preconditions, and classifying why a run failed | JEV (page text; the screenshot too when egress is `hybrid`) |
| Judging how the page looks (photos, icons, colours, layout) | JEV when screenshots may leave the machine (egress `hybrid`), else the local model |

Each decision goes to JEV with page text only first. When the egress policy is
`hybrid` (the default) and JEV is unsure of its answer (confidence below 0.5), the same
question is asked again with the screenshot.

Each candidate element is described with where it is on the screenshot: the screen
region and the approximate centre in CSS pixels, the heading above it, its rank among
elements with the same role and name (with row and column when they form a grid), and
whether it sits in an iframe or a shadow root. For example:

```text
button "Add to cart" at top-right (x 1180, y 340), under "Backpack", #2 of 6 alike (row 1, col 2)
```

This lets JEV tell look-alike elements apart and keeps it from favouring the first
element in the list.

Every element on the page is offered, in page order: nothing is ranked or dropped by
matching words. When the list holds more than 32 KB of text, where JEV switches to a
slower and less accurate long-context mode, Hercules splits it into parts in page order,
asks JEV about each part (a few at a time), then asks once more which of the parts'
picks is the one. A page with more than 254 elements (the most one question can offer)
is asked in groups the same way. The run summary counts every call. The local model in
pure mode gets the same list, split the same way, but at a smaller size: a quarter of the
model's context window (16 KB of text on a 16k-token model, at most 32 KB), because a local
model reads a long list slowly and less reliably.

If JEV cannot answer a decision (network down, timeout, a refused or malformed reply),
the local model answers that one decision. The run continues, and the result records
how many decisions fell back (`engines.fallbacks` in `run.json`). After repeated
failures Hercules stops calling JEV for a few minutes and uses the local model.

Hybrid mode still needs the local model server: it does all the language work and covers
JEV outages.

### Pure mode

The local model does everything:

- reads each step and turns it into actions;
- picks the tool and the element for each action;
- judges whether a step worked, outcomes compared by meaning, preconditions and why a
  run failed, using the screenshot when the model server accepts images.

Nothing is sent anywhere except to your model server. If you run a local Clef server
(see [A local decision model: Clef](#a-local-decision-model-clef)), it makes the
decisions instead, and the local model does the rest.

### How the mode is chosen

| `--mode` | Result |
|---|---|
| `auto` (default) | `decider.backend` from settings if set (`local` or `clef` means pure, `jev` means hybrid). Otherwise hybrid when an OpenRouter key is found and the egress policy is `hybrid-text` or `hybrid` (the default); else pure. |
| `hybrid` | Hybrid mode. Fails before the run starts if the egress policy is `local` or no key is found. |
| `pure` | Pure mode |

Without a key, a default run is pure and says so in one line, for example:

```text
notice: hybrid mode (JEV decides) needs an OpenRouter key and none is configured; this run is pure: the local model makes every decision. Add a key with `hercules setup`.
Mode: pure [auto: OPENROUTER_API_KEY is not set] - local model ... decides and interprets (with images)
```

The mode and the reason are printed before every run. `hercules doctor` shows the mode
a default run would use, and where the key comes from (never the key itself).

Both modes need the local model server, because it reads the steps. When it cannot be
reached, the run stops before its first step with exit code 2 and points to
`hercules doctor` and `hercules setup`.

## The OpenRouter key

Hercules looks for the key in this order:

1. the environment variable `OPENROUTER_API_KEY` (or the one named by `--jev-key-env`
   or `decider.api_key_env`);
2. the file `~/.hercules/openrouter.env`, with one line `OPENROUTER_API_KEY=...`. Only
   you may read it (`chmod 600`); a file others can read is ignored with a warning.

`hercules setup` writes that file for you: it reads the key without showing it, checks
it with one tiny JEV request (it costs a few millionths of a dollar), and saves it with
mode 600. Never put the key in a `config.yaml`: Hercules refuses configuration keys that
look like secrets.

A key from the file is passed to JEV only. It is not added to the environment your test
cases can read.

For runs through the daemon (the UI and coding agents), the daemon reads the same file,
or the variable from the environment `hercules serve` was started in.

## Egress policy

The egress policy says what may leave the machine. It applies to every model call: the
local model server and JEV.

| Policy | What may leave the machine |
|---|---|
| `hybrid` (default) | Page text, and screenshots when JEV is unsure or a step asks how the page looks |
| `hybrid-text` | Page text (the element list and step text) may go to JEV. No screenshots. |
| `local` | Nothing. Hybrid mode is refused, and a model server on a public address is refused. |

The default only matters once a key is configured: without a key nothing goes to JEV.

Set it in settings, as an environment variable, or per run:

```yaml
# .hercules/config.yaml or ~/.hercules/config.yaml
egress:
  policy: local
```

```bash
export HERCULES_EGRESS=hybrid-text
hercules run .hercules/cases/sign-in.json --egress local
```

Only this machine counts as local: loopback addresses (`127.0.0.0/8`, `::1`) and
`localhost`. Hercules decides this from the URL alone, without DNS lookups. Another
machine, even one on your private network (a `10.x` or `192.168.x` address, a `.local`
name, `host.docker.internal`), is remote unless you list it:

```yaml
egress:
  policy: local
  local_hosts: [10.0.0.5, gpu-box.local]   # host names or IP addresses, no scheme or port
```

A listed host is trusted like this machine, so under `local` page text and screenshots
can reach it; list only machines you control, and prefer `https` for them. Traffic to
this machine and to listed hosts never goes through an `HTTP_PROXY` / `ALL_PROXY` proxy.

What never leaves the machine in any mode: your case files, the evidence folder, and
the secret values in your test data (models see the `{{placeholder}}`, not the value).

### Files for upload steps

Files that upload steps attach ([Files for upload steps](writing-tests.md#files-for-upload-steps))
are never read by any model, in any mode: they go only to the app under test. The same holds
for files a run downloads ([Downloading and checking files](writing-tests.md#downloading-and-checking-files)):
their checks run in code, and their contents never reach a model.

## Credentials in URLs

A session id or token in an address (`/login?sid=...`, `#access_token=...`, a password in
`https://user:password@host`) is treated like a secret, even when the case did not declare
it as one:

- Before the run, each such value in a step's text is replaced with a placeholder:
  `I open "https://app.example/login?sid=a1B2c3..."` becomes
  `I open "https://app.example/login?sid={{url_credential_1}}"`. Models, the event
  stream, the UI and `run.json` see the placeholder; the browser gets the real address
  when the step runs.
- Any address written out or sent to a model has the values of credential parameters
  masked by name, wherever it comes from (the page's own address after a redirect, a link,
  a network request): `https://app.example/home?sid=[REDACTED]&tab=2`. The rest of the
  address stays readable. The names are a fixed redaction list, compared without case:
  `sid`, `session`, `sessionid`, `jsessionid`, `token`, `access_token`, `id_token`,
  `refresh_token`, `auth`, `authorization`, `code`, `key`, `api_key`, `apikey`,
  `password`, `pwd`, `secret`, `signature`, `sig`, `X-Amz-Signature`, `jwt` and similar.
- This covers `run.json`, the copy of the case in the run folder, `plan.jsonl`,
  `decisions.jsonl`, `observation.txt`, `junit.xml`, `network.jsonl`, `console.jsonl`,
  `worker.log`, the daemon's events and the UI, MCP results, and every request to a model
  (step readings, decisions, judgements).

The copy of the case in the run folder is masked, so `hercules worker --resume` of a run
whose case had such a URL needs `--case` with the original file. The daemon keeps the
real case outside the run folder while the worker runs and removes it afterwards.
Exported tests read a URL credential from an environment variable named after its
placeholder (`URL_CREDENTIAL_1`). For a login link you reuse, prefer a secret reference
(see [Writing tests](writing-tests.md#secrets)).

## Zero data retention

Every JEV request through OpenRouter asks for providers that keep no data and do not
collect it (`provider: {"zdr": true, "data_collection": "deny"}`). OpenRouter accepts
this for JEV. To stop asking (for example if a future provider change makes requests
fail), turn it off:

```yaml
decider:
  zdr: false
```

or `HERCULES_DECIDER_ZDR=0`. The run header and `hercules doctor` say whether zero data
retention was requested.

Screenshots go to JEV inline in the request. (The TypeSafe `assets` form of sending
images is ignored by OpenRouter's endpoint, so Hercules does not use it there.)

## More hybrid settings

```yaml
decider:
  backend: jev            # always hybrid; omit to choose per run
  api_key_env: MY_JEV_KEY # the key is in this variable instead of OPENROUTER_API_KEY
  zdr: true
egress:
  policy: hybrid-text     # never send screenshots
```

After a hybrid run, Hercules prints the number of JEV calls, how many carried a
screenshot, how many failed, the cost in USD, and the local fallbacks. The cost is also in
`run.json` under `cost.usd`.

## A local decision model: Clef

Clef and Clef-flash are open-weight decision models (Apache-2.0, on Hugging Face as
`Cloudflare/clef` and `Cloudflare/clef-flash`) that answer the same requests as JEV. If
you serve one on your machine (or on a machine listed in `egress.local_hosts`), pure-mode
runs use it for decisions:

```yaml
decider:
  clef_url: http://127.0.0.1:30000   # or HERCULES_CLEF_URL; decider.backend: clef uses this address by default
  clef_model: Cloudflare/clef-flash  # the model id the server expects (default)
  clef_images: false                 # send screenshots to Clef (off until verified)
```

When the server answers, the run header says `Clef ... decides`, and the local model
interprets steps and answers any decision Clef cannot. Clef's decisions are counted as
`engines.clef_calls` in `run.json` (not as JEV calls). When it does not answer, the run
uses the local model and prints a note. Clef's confidence threshold for accepting an
element is the default 0.6 until it is calibrated. Clef needs no key and nothing leaves the
machine; a Clef URL that is not on this machine or a listed local host is refused. `hercules doctor` asks the server one
tiny question and reports the result.

### Serving Clef-flash locally

> These steps follow the model card. They have not yet been run on a Hercules
> development machine. Clef-flash is a 9B model (about 18 GB in BF16); check your memory
> before you download it. Hercules never downloads it for you.

**With an NVIDIA GPU (Linux), SGLang in Docker** (the model card's recommended server;
it serves the decisions API at `/v1/systemone`). The card binds `0.0.0.0`; this command
binds `127.0.0.1` so the server is reachable only from this machine:

```bash
docker run --gpus all --shm-size 32g -p 30000:30000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface --ipc=host \
  lmsysorg/sglang:dev-clef \
  sglang serve --model-path Cloudflare/clef-flash --host 127.0.0.1 --port 30000
```

Then check it and point Hercules at it:

```bash
curl http://127.0.0.1:30000/v1/systemone -H 'Content-Type: application/json' \
  -d '{"model":"Cloudflare/clef-flash","state":"ping","questions":{"ok":{"type":"noul","instructions":"Is this a test?"}}}'
export HERCULES_CLEF_URL=http://127.0.0.1:30000
hercules doctor
```

**Without a supported GPU (for example Apple Silicon)**: the decisions API needs the
model's own decision head (`joint_head.safetensors` with `joint_schema_model.py` from the
model repository), which generic chat servers (vLLM's chat endpoint, llama.cpp, MLX,
Ollama, and the quantised GGUF builds) do not provide. The model card's Python path
loads it with `transformers`; a small HTTP wrapper turns that into a local server:

```python
# clef_server.py: serves POST /v1/systemone on 127.0.0.1:30000 (not yet tested on Apple Silicon)
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from huggingface_hub import snapshot_download

path = snapshot_download("Cloudflare/clef-flash")
sys.path.insert(0, path)
from joint_schema_model import load_release_model, systemone  # noqa: E402

model, processor = load_release_model(path, device="mps")  # "cuda" on NVIDIA, "cpu" otherwise


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/systemone":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        answer = json.dumps(systemone(model, processor, body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)


HTTPServer(("127.0.0.1", 30000), Handler).serve_forever()
```

```bash
pip install torch transformers pillow huggingface_hub
python clef_server.py
```

The model card was tested with `torch` 2.11 and `transformers` 5.10.2 on a single NVIDIA
H200; whether the decision head runs on Apple's `mps` device is not documented. Keep
`clef_images: false` until you have checked how your server reads images.

## Hosted models in place of the local model

The local-model role can also point at a hosted, OpenAI-compatible service
(`llm.provider: openrouter`, `openai`, `anthropic`, `azure`, `openai-compatible` with a
public URL, or `litellm` for a LiteLLM proxy). The key is read from the variable named by
`llm.api_key_env` (defaults: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`AZURE_OPENAI_API_KEY`, `LITELLM_API_KEY`). See
[Use any OpenAI-compatible endpoint](getting-started.md#use-any-openai-compatible-endpoint).

Whether such an endpoint is local or remote is decided from its URL, as above. A remote
endpoint is refused under the egress policy `local`. Under `hybrid-text` and `hybrid` it
receives everything the local model would see: your step text, the page's element list
and text, and, when images are on (the model accepts them and the policy is `hybrid`),
screenshots. A proxy such as LiteLLM forwards them on to whichever service it routes the
model to, so the same applies to every service behind it.

## Telemetry

Hercules sends anonymous usage telemetry by default, to help us see which setups work
and where runs fail. The first `hercules` command you run prints a notice saying what is
sent and how to opt out, once; `hercules setup` shows it too and asks whether to keep it
on.

What is sent:

- events: `app_started`, `doctor_completed`, `run_started`, `run_finished`,
  `step_failed`, `error`, `agent_setup` (when `hercules init` or `hercules setup`
  connects a coding agent), and `explore_started` / `explore_finished` (an exploration
  session's size, time budget, how it ended and how many clues and findings it had,
  never what they were);
- the Hercules version, the Python version (major and minor), and the OS family
  (`darwin`, `linux`, `windows` or `other`);
- the configured LLM provider, model tier, decision backend and egress policy;
- step counts, run status, durations in milliseconds, and error class names (for
  example `CaseTimeout`);
- where a run was started: `cli` (`hercules run`), `daemon` (the tester UI or HTTP API)
  or `agent` (a coding agent over MCP), and for an agent its fixed name: `claude-code`,
  `codex`, `cursor`, `copilot`, `gemini` or `other` (never the name the agent reports);
- a random installation id, stored in `~/.hercules/installation_id`.

Under the egress policy `local` telemetry is always off: no events are sent and no
installation id is created, whatever `telemetry.enabled` says.

What is never sent: test cases, step text, prompts, page content, screenshots, URLs,
file paths, host names, user names, emails or secrets. Every event is checked against a
fixed list of fields before it is queued; text that looks like a URL, path, email or
token is refused. Events are delivered in the background and never slow Hercules down,
even offline.

### Where it goes

Events are sent to [PostHog](https://posthog.com) (US cloud, `us.i.posthog.com`), one
HTTPS request per event, with Hercules's own HTTP client; no PostHog library is
installed. Each event is anonymous:

- its distinct id is the random installation id, nothing else identifies you;
- `$process_person_profile` is `false`, so PostHog creates no person profile;
- `$geoip_disable` is `true`, so no location is derived from your IP address;
- the TestZeus PostHog project discards client IP addresses, so they are not stored.

The request carries a `User-Agent` of `testzeus-hercules/<version>` and nothing else from
your machine. Proxy settings in `HTTPS_PROXY` and similar variables are honoured.

### Opt out

Any of these turns telemetry off:

```bash
hercules --no-telemetry run case.json     # this command only, and anything it starts
hercules run --no-telemetry case.json     # the flag also works after the command
export HERCULES_TELEMETRY=0               # this shell
export DO_NOT_TRACK=1                     # the cross-tool convention; HERCULES_TELEMETRY wins if both are set
hercules setup --telemetry off            # saved in ~/.hercules/config.yaml
```

```yaml
# ~/.hercules/config.yaml (all projects) or <project>/.hercules/config.yaml
telemetry:
  enabled: false
```

`HERCULES_TELEMETRY` also accepts `false`, `no` and `off`; `egress.policy: local` turns
it off too. `hercules doctor` shows whether telemetry is on. The notice is remembered in `~/.hercules/telemetry_notice_shown`.

## Links to TestZeus

The tester UI links to [TestZeus](https://testzeus.com): a **Go Pro with TestZeus** card in
the sidebar (a free trial and the pricing page) and **Made by TestZeus** under it. These links
carry `utm_source=hercules`, `utm_medium` and `utm_campaign` and nothing else: no project,
run, case or user detail. Nothing is sent unless you open one. The card's **×** hides it; a
single **Try TestZeus Pro** link stays in its place. The hidden state is kept in that browser.
