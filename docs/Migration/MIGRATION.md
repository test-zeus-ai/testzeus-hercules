# LangGraph Migration Notes

This guide documents the operational changes introduced by the LangGraph
migration. It is intended for users upgrading configuration and for maintainers
reviewing behavior that changed from the AG2 implementation.

This is not a benchmark report. Historical AG2-vs-LangGraph run statistics
belong in a separate evaluation document; this file describes the compatibility
and runtime changes required to operate this branch.

## What Changed

- Orchestration moved to `SimpleHercules`, backed by a LangGraph `StateGraph`.
- The runtime graph has explicit `planner`, `executor`, and `assertion` nodes.
- Navigation helpers use LangChain `StructuredTool` objects instead of the old
  group-chat tool dispatch model.
- LLM setup supports direct `LLM_MODEL_*` values for simple runs and
  `agents_llm_config.json` plus an active provider/profile key for per-agent
  routing.
- MCP tools are discovered asynchronously and wrapped as dynamic
  `StructuredTool` instances.
- Token usage and cost metadata are accumulated from LangGraph planner and
  helper calls.

## Dependency and Runtime Targets

The package metadata supports Python `>=3.11,<3.14`. This branch's CI/runtime
target is Python 3.13.

Migration-relevant dependencies include:

- `langgraph>=0.2.0,<0.4`
- `langchain-core>=0.3.0,<0.4`
- `langchain-openai>=0.2.0,<0.4`
- `langchain-community>=0.3.0,<0.4`
- `mcp>=1.23.3,<2`
- `httpx>=0.28.1,<0.29`
- `pydantic>=2.6.2,<3`

Install with the repository's normal package manager flow, then install
Playwright browsers:

```bash
playwright install --with-deps
```

## Config Migration

Use `agents_llm_config.json` when you need different model settings for
planner, navigation, memory, and helper roles:

```json
{
  "litellm": {
    "planner_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "replace-me",
      "model_base_url": "https://api.openai.com/v1",
      "llm_config_params": {
        "temperature": 0,
        "seed": 12345
      }
    },
    "nav_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "replace-me",
      "model_base_url": "https://api.openai.com/v1",
      "llm_config_params": {
        "temperature": 0,
        "seed": 12345
      }
    },
    "mem_agent": {
      "model_name": "gpt-4o-mini",
      "model_api_key": "replace-me",
      "model_base_url": "https://api.openai.com/v1",
      "llm_config_params": {
        "temperature": 0,
        "seed": 12345
      }
    },
    "helper_agent": {
      "model_name": "gpt-4o",
      "model_api_key": "replace-me",
      "model_base_url": "https://api.openai.com/v1",
      "llm_config_params": {
        "temperature": 0,
        "seed": 12345
      }
    }
  }
}
```

Set both of these values:

```bash
export AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
export AGENTS_LLM_CONFIG_FILE_REF_KEY=<provider-key>
```

The ref key must match the top-level provider/profile name in the file. The
branch currently includes `agents_llm_config-example copy.json.txt` as a sample
shape; create or copy it to `agents_llm_config.json` before running. The sample
uses `litellm` as one possible OpenAI-compatible proxy profile, but that key is
not mandatory.

Direct config through `LLM_MODEL_NAME`, `LLM_MODEL_API_KEY`, and related direct
`LLM_MODEL_*` environment variables is still accepted for simple single-model
runs. Direct CLI flags such as `--llm-model` and `--llm-model-api-key` follow
the same path.

Quick mapping:

| Old setup | LangGraph branch setup |
| --- | --- |
| `LLM_MODEL_NAME` | `agents_llm_config.json` profile bucket `planner_agent` / `nav_agent` / `mem_agent` / `helper_agent` |
| `LLM_MODEL_API_KEY` | `model_api_key` inside the active profile bucket |
| `LLM_MODEL_BASE_URL` | `model_base_url` inside the active profile bucket |
| `--llm-model` | Direct single-model setup, or `--agents-llm-config-file` plus `--agents-llm-config-file-ref-key` for per-agent routing |
| One shared direct model | Per-agent model buckets, with `nav_agent` shared by navigation helpers |

Use environment variables:

```bash
export AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
export AGENTS_LLM_CONFIG_FILE_REF_KEY=<provider-key>
testzeus-hercules --project-base=opt
```

Or pass the same values through CLI:

```bash
testzeus-hercules \
  --project-base=opt \
  --agents-llm-config-file agents_llm_config.json \
  --agents-llm-config-file-ref-key <provider-key>
```

## Orchestration Contract

The planner must return strict JSON. `is_passed` is a boolean, not `null`:

```json
{
  "plan": "Complete the account signup flow.",
  "next_step": "Open the signup page.",
  "target_helper": "browser",
  "terminate": "no",
  "final_response": "",
  "is_assert": false,
  "assert_summary": "",
  "is_passed": false
}
```

Assertion behavior:

- `is_assert=true` with `target_helper="Not_Applicable"` routes to the
  assertion node.
- The assertion node trusts `is_passed` and `assert_summary`.
- Missing or falsey `is_passed` becomes `false`.
- `terminate="yes"` ends the graph directly.

Planner round exhaustion and planner LLM timeouts return failed
assertion-style results instead of hanging or producing an ambiguous status.

## Helper Execution Semantics

The executor maps `target_helper` values to helper agents:

| `target_helper` | Runtime helper |
| --- | --- |
| `browser` | `browser_nav_agent` |
| `api` | `api_nav_agent` |
| `sec` | `sec_nav_agent` |
| `sql` | `sql_nav_agent` |
| `time_keeper` | `time_keeper_nav_agent` |
| `mcp` | `mcp_nav_agent` |
| `executor` | `executor_nav_agent` |
| `agent` | `browser_nav_agent` |

Before each browser helper step, Hercules refreshes the live current URL from
Playwright and injects it into the helper task as `Current Page: ...`. This
prevents a stale URL in graph state from steering the helper after navigation.

When dynamic long-term memory is enabled, memory is queried for the exact
helper task and injected as `EXTRA INFORMATION`. Helper output containing
`##FLAG::SAVE_IN_MEM##` is persisted to memory.

Repeated completed planner steps are detected and logged, but they do not force
success. The planner still has to produce a real assertion verdict.

## Browser Tool Guard

The LangGraph executor can receive multiple tool calls from one helper LLM
response. For browser helpers, some calls mutate page state and make later
calls stale.

When a browser tool changes state, Hercules skips remaining tool calls from
that assistant response and asks the helper to re-read the page/DOM. This guard
applies to state-changing tools such as `open_url`, `click`, `bulk_enter_text`,
`bulk_select_option`, `bulk_set_date_time_value`, `bulk_set_slider`,
`click_and_upload_file`, `drag_and_drop`, `entertext`, `hover`,
`press_key_combination`, and `set_current_geo_location`.

This behavior is migration-critical. It preserves the benefit of native tool
calling while avoiding AG2-era assumptions that every proposed call can safely
run against the same DOM snapshot.

## Tool Schema Changes

Tools are converted to LangChain `StructuredTool` objects with Pydantic
argument schemas. Provider-safe schemas are part of the public tool contract.

Use:

- scalar inputs
- `List[Dict[str, str]]` for bulk browser actions
- explicit empty schemas for no-argument tools

Avoid:

- public tuple parameters
- schemas that generate `prefixItems`
- stale browser arg names such as `selector_text_list`
- wrapper-only `kwargs` schemas

Migration note for maintainers: old helper code often tolerated loose kwargs or
tuple-shaped inputs because tool dispatch was mediated by AG2. The LangGraph
path binds tools directly to provider-facing LangChain schemas, so those loose
public shapes can fail before tool code runs.

## MCP Lifecycle

MCP support now has two sides.

Client-side MCP helper:

- Enable with `MCP_ENABLED=true`.
- Point `MCP_SERVERS` to a JSON config containing `mcpServers`.
- Supported transports are `stdio`, `sse`, and `streamable-http`.
- `SimpleHercules` awaits helper readiness through `ensure_tools_ready()`
  before binding MCP tools to the LLM.
- `MCPHelper` initializes sessions, lists server tools, and creates dynamic
  `StructuredTool` wrappers named `mmcp_<server>_<tool>`.
- Each wrapper preserves the MCP server tool's input schema.
- Shutdown calls `MCPHelper.destroy()` to close sessions, client contexts, and
  HTTP clients.

Server-side Hercules MCP:

```bash
testzeus-hercules-mcp
```

This starts a FastMCP streamable HTTP server, defaulting to
`http://0.0.0.0:8000/mcp`. It exposes tools including `generate_gherkin`,
`run_test`, and `get_test_results`.

Environment variables for the server entrypoint:

- `TESTZEUS_ROOT`: repo/project root for generated features and output
- `TESTZEUS_PYTHON`: Python executable used to invoke Hercules
- `MCP_HOST`: bind host, default `0.0.0.0`
- `MCP_PORT`: bind port, default `8000`
- `MCP_PATH`: HTTP path, default `/mcp`

## Token and Cost Reporting

LangGraph token totals include planner LLM calls and navigation helper LLM
turns. Executor entries aggregate all helper turns used for a planner step.

Final cost reporting is shaped like:

```json
{
  "usage_including_cached_inference": {
    "langgraph": {
      "prompt_tokens": 7,
      "completion_tokens": 11,
      "total_tokens": 18
    }
  }
}
```

When providers or gateways do not expose cost metadata, Hercules reports
`cost_unavailable=true` at both the top usage level and under `langgraph`.
This is not a failure; it means token counts are available but price data was
not returned by the provider.

## Compatibility Notes

- `AGENTS_LLM_CONFIG_FILE` and `AGENTS_LLM_CONFIG_FILE_REF_KEY` must be set
  together when the config-file path is used.
- The active ref key must match a top-level profile in
  `agents_llm_config.json`.
- `LLM_MODEL_*` env vars and direct `--llm-model*` CLI flags remain valid for
  direct single-model configuration.
- Navigation models must support tool calling.
- Planner models must be reliable at strict JSON output.
- Tool-call loops are bounded by nav max rounds and return explicit errors
  when `##TERMINATE TASK##` is never reached.
- Full raw DOM dumps should not be treated as the default browser sensing
  contract; compact DOM/text tools are the provider-safe path.
