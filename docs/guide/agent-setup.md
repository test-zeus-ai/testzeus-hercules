# Set up Hercules with your coding agent

> **Watch it:** [One line to install and set up (1:19)](videos.md#1-one-line-to-install-and-set-up).

Copy the prompt below into Claude Code, Codex, Cursor, GitHub Copilot, Gemini CLI or any
other coding agent, in your app's project folder. The agent looks around first, asks you a
few questions, installs and sets up Hercules, runs a first test of your app, and from then
on tests its changes through Hercules.

Your API key never goes through the agent: if you choose a hosted model, the agent asks you
to enter the key in your own terminal, where it is read without being shown.

<!-- prompt:start -->
```text
Set up Hercules by TestZeus (open-source end-to-end testing) for this project.

1. Look first, don't ask yet: my OS and memory, whether `uv` is installed,
   whether a local model server (Ollama, LM Studio, ...) is running, which
   coding agents this project uses, and the URL this app runs on locally.
2. Then ask me, one question at a time, with your best guess as the default:
   - Which URL should Hercules test?
   - Which model: a local server, OpenRouter, or another OpenAI-compatible
     endpoint (then also its URL and model id)?
   - Which browsers to test in: Chromium (default), Firefox, WebKit, Chrome, Edge?
   - Which coding agents should be connected to Hercules?
   - Keep anonymous usage telemetry on?
3. Never ask me for an API key, and never put one in a command, file or chat.
4. If `uv` is missing, ask before installing it with its official installer.
   Then run, with my answers filled in:
     uvx testzeus-hercules --yes --project . --browsers <browsers> --agents <agents> --telemetry <on or off>
   adding, for OpenRouter: --provider openrouter --egress hybrid
   for another endpoint: --provider openai-compatible --base-url <URL> --model <id> --egress hybrid
   for a local server only: --egress local
   (`uvx testzeus-hercules setup --help` lists every setup flag.)
5. For OpenRouter or another endpoint, ask me to run the same `hercules setup`
   flags (without --yes) in my own terminal: it asks for the key without
   showing it, and Enter keeps the other answers. Wait until I say it's done.
6. Run `hercules doctor` and fix what it reports, asking me before any change.
   If I chose a local server and none is running, help me install and start one.
7. Write one smoke test for my app's main page, run it with `hercules run`
   (see `hercules run --help`), and show me the result and the evidence folder.
8. Tell me to restart you so the Hercules tools load. From then on, use
   Hercules for every end-to-end test in this project.
```
<!-- prompt:end -->

## What the agent runs

The one line it runs is the same one you can run yourself:

```bash
uvx testzeus-hercules
```

With no command, `testzeus-hercules` asks before each change:

1. **The `hercules` command.** `uvx` runs Hercules from a temporary environment, so it offers
   to install the same version for good with `uv tool install` (or `pipx install` without uv),
   and to put uv's tool folder on your `PATH`.
2. **Browsers**: it lists Chromium, Firefox, WebKit, Chrome and Edge with what is already set
   up, and asks which to set up. The default is Chromium plus any browser your saved
   environments use (`.hercules/envs/`). Chrome and Edge are installed for the whole
   machine, so Playwright's installer may ask for your password.
3. **`hercules setup`**: the model (a local server, OpenRouter, or another OpenAI-compatible
   endpoint with its URL and key), the optional OpenRouter key for hybrid mode, the egress
   policy, telemetry, and the coding agents to connect (see
   [Guided setup](getting-started.md#guided-setup-hercules-setup)).

Every flag that is not one of its own goes to `hercules setup`, so `--yes` answers every
question with its default and `--agents claude` connects Claude Code without asking. Its own
flags:

| Flag | What it does |
|---|---|
| `--no-install` | Do not install the `hercules` command |
| `--browsers LIST` | Set up these browsers without asking: comma-separated `chromium`, `firefox`, `webkit`, `chrome`, `msedge`, or `none` (with `--yes`: Chromium and the ones your environments use) |
| `--no-browser` | The same as `--browsers none` |
| `--from SPEC` | Install this instead of the running version: a version pin, a wheel or a git URL (also `HERCULES_INSTALL_FROM`) |

With a command, `uvx testzeus-hercules <command>` is the same as `hercules <command>`.

No uv? `pipx run testzeus-hercules` does the same, or install uv with its
[official installer](https://docs.astral.sh/uv/getting-started/installation/) first.

## If something goes wrong

- **`hercules: command not found` after the install.** Open a new terminal, or run
  `uv tool update-shell` and then open one.
- **Chromium does not start on Linux.** It may need system libraries:
  `sudo python -m playwright install-deps chromium`.
- **The agent cannot see the Hercules tools.** Restart the agent in the project folder. Check
  that the file for your agent exists (see [Coding agents](coding-agents.md)) and that
  `hercules doctor` passes.
