# Hercules v2 (PR #188): manual testing findings

Tested on `hercules_bau` @ `4647656` (2.0.0.dev0), macOS, 24 GiB (tier `standard`), Ollama 0.40.13,
local model `gemma4:e4b`, pure mode.

What was exercised: `hercules doctor`, tester UI (`serve` + `ui`) against the demo shop
(`bench/explore`), a native Android app on BrowserStack, Sauce Labs connection, and a Salesforce
case through plan review.

Status: **Confirmed** = reproduced; **To verify** = seen in code or output, not yet reproduced.

## Setup and models

### 1. Default local setup fails `hercules doctor` out of the box — Confirmed
- Ollama's default context is 4096 tokens; tier `standard` needs 16384, so `context` FAILs.
- gemma4 spends the probe's output on reasoning, so `tool_calling`, `json_schema` and `images`
  are reported as "not measured".
- Needed two manual changes: `OLLAMA_CONTEXT_LENGTH=16384` (or the Ollama app's context setting)
  and `llm.request_params: {reasoning_effort: none}`.
- Suggestion: `hercules setup` detects and applies both, or explains them up front.

### 2. Wrong fix hint when the model server returns HTTP 402 — Confirmed
- Ollama cloud models (`glm-5.2:cloud`, `minimax-m3:cloud`) on an account with no credits:
  doctor reports `tool request rejected (HTTP 402)` with fix "upgrade Ollama to 0.32.1 or newer".
- Actual server error: "Add usage credits or subscribe … https://ollama.com/plans".
- Suggestion: surface the server's error message instead of a version hint.

### 3. Egress policy treats Ollama `:cloud` models as local — To verify
- Doctor: `policy 'hybrid' allows this local endpoint` for `glm-5.2:cloud`, because the URL is
  `127.0.0.1:11434`. Ollama forwards those prompts to its cloud.
- If `egress: local` behaves the same, "nothing leaves my machine" is not guaranteed.

### 4. Model / OpenRouter key cannot be configured from the tester UI — Confirmed
- The **Models** screen is read-only. Cloud credentials can be added and tested in the UI, but the
  model, provider and OpenRouter key (hybrid mode) need `hercules setup` or config.yaml.

### 5. `tool_choice` ignored on Ollama — Info
- Doctor WARN; Hercules steers tool use through the prompt. Worth watching if steps pick the wrong
  tool.

### 6. Telemetry on by default with a long first-run notice — Info
- Consider an explicit opt-in prompt for the community release.

### 7. `--no-telemetry` not accepted after `cloud check` — Confirmed (minor)
- `hercules doctor --no-telemetry` works; `hercules cloud check saucelabs --no-telemetry` fails with
  `unrecognized arguments: --no-telemetry` (only accepted before the subcommand).

## Runs and evidence

### 8. Missing element classified as `test_fragility` — Confirmed
- Case asked for an "Email" field that does not exist on the page (only "Name"). Hercules correctly
  refused (`no_match` ×3), but the failure category was `test_fragility` (0.8).
- A case/app mismatch is not flakiness; a category like "case does not match the app" would be
  clearer.

### 9. Semantic verify records no reasoning — Confirmed
- Mobile run `verify: I should see the home page of the app` passed via the local model, but
  `steps/3/decisions.jsonl` is one line: `{"engine": "local-llm", "probabilities": [1.0], "purpose": "semantic"}`.
- No reason or criteria recorded, so a pass on a loose expectation cannot be audited. Web target
  decisions do record options and confidence.

## Device clouds

### 10. Sauce Labs region cannot be selected — Confirmed (commented on PR) — Fixed on this branch
- Fix: Region select on the Sauce Labs card (`PUT /api/clouds/saucelabs/region`), `--region` on
  `hercules cloud connect saucelabs`, `SAUCE_REGION` env var, and a 401 check that names the region
  that accepts the keys. Precedence: `SAUCE_REGION` > saved in `clouds.env` > `mobile.saucelabs.region` > `us-west-1`.
- EU account (`eu-central-1`). UI card shows "Region us-west-1" and the check fails with
  "Sauce Labs refused the credentials (HTTP 401): check the user name and access key".
- Same key: 200 on `eu-central-1`, 401 on `us-west-1` and `us-east-4`.
- Only fix today is `mobile.saucelabs.region` in config.yaml. No UI field, CLI option or env var.
- Suggestion: Region select in the UI and `--region` on `hercules cloud connect saucelabs`; mention
  the region in the 401 message.
- https://github.com/test-zeus-ai/testzeus-hercules/pull/188#issuecomment-6912689718

### 11. Exported Appium tests hardcode the Sauce US hub — To verify
- `testzeus_hercules/export/appium.py:51`: `"saucelabs": "https://ondemand.us-west-1.saucelabs.com:443/wd/hub"`.
- If the configured region does not override it, exported tests fail for EU / us-east accounts.

### 12. Sauce web runs need `saucectl` — Info
- Sauce card shows Web "not ready" until `npm i -g saucectl` is installed; native apps are ready.

## Cases and plans

### 13. Environment accepts a credential in the Base URL without warning — Confirmed
- A Salesforce front-door URL with a live `sid=` saved fine in **Environments** and is stored in
  plain text in `.hercules/envs/salesforce.json` and copied into the run's `case.json`.
- Plan lint then blocks the run with two `credential_in_url` errors.
- Suggestion: warn (or offer a secret reference) when saving the environment, not only at run time.

### 14. `.feature` cases cannot use secrets — Confirmed
- `.feature` files have no `test_data`, so a front-door token cannot be referenced as a secret;
  the case has to be converted to JSON. The lint fix hint ("move it to test_data") does not say
  this.

### 15. Unrecognised v1 step reported on the wrong step — Confirmed
- `I save "Test_Account_" + timestamp in dd-MM-yyyy_hh-mm-ss format as <temp_account_name>` is
  planned as `unknown do` with no lint error on that step; the error only appears later as
  `unresolved_placeholder` on step 4.
- Suggestion: lint the unknown action itself and point to `{{timestamp}}` / `test_data`.
