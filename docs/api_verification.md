# Deterministic API verification tools

The `api_nav_agent` can verify responses in code instead of judging them by eye. The tools live in `testzeus_hercules/core/tools/` and are available when the API agent runs. Most of them need the `api` extra:

```bash
pip install 'testzeus-hercules[api]'   # jsonpath-ng + openapi-schema-validator
```

Without the extra the tools return a structured `ERROR` verdict that names the missing package.

## Tools

| Tool | Purpose |
| --- | --- |
| `generic_http_api` | Send an HTTP request (any method, auth type, body mode). |
| `extract_from_response` | Capture a JSONPath value into `${name}` for later calls and assertions. |
| `assert_json_path` | Assert a value with an operator and get an explicit `PASSED`/`FAILED`/`ERROR` verdict. |
| `validate_response_schema` | Validate a body (or a sub-value) against a JSON Schema. |
| `validate_against_openapi_spec` | Validate a body against the response schema declared in an OpenAPI/Swagger spec. |
| `run_hurl_suite` | Run a pre-written Hurl suite (needs the `hurl` binary). |
| `assert_ui` / `capture_ui_value` | Assertions over page text. **Opt-in**, see below. |

Every `assert_json_path` and `validate_against_openapi_spec` verdict (passes included) is appended to `proofs/assertion_logs.log`. `validate_response_schema` and `extract_from_response` return their verdict to the agent but write nothing to that ledger (a capture is not an assertion, and a schema check is not counted by the report). Values in that ledger are redacted: a secret-named path or capture name (`password`, `token`, `secret`, `api_key`, ...) hides the value, and the values of secret-named captured variables are scrubbed from the text.

## Flags

| Variable | Default | Effect |
| --- | --- | --- |
| `API_REQUEST_TIMEOUT` | `5` | HTTP timeout in seconds for `generic_http_api` (the long-standing default). |
| `API_ALLOWED_PATHS` | empty | Extra folders (separated by `:` on POSIX, `;` on Windows) that model-supplied file paths may live in, on top of the test data folder and the project root. |
| `ENABLE_UI_ASSERTIONS` | `false` | Register `assert_ui` and `capture_ui_value` on the browser, MCP and mobile agents and add a short guidance block to their prompts. Set it before startup. |

## Path confinement (always on)

Paths supplied by the model are resolved with `realpath` (so `..` and symlinks are followed) and must sit inside `TEST_DATA_PATH`, `PROJECT_SOURCE_ROOT` or an `API_ALLOWED_PATHS` folder. This applies to:

- the `file_path` of a multipart upload in `generic_http_api`,
- `hurl_path` of `run_hurl_suite` (including every `.hurl` file inside a directory),
- `spec_path` of `validate_against_openapi_spec`,
- `open_api_spec_path` of the nuclei security tools.

A path outside the roots returns an `Access denied` error and nothing is read. Relative paths are looked up under the test data folder first. Even inside the roots, secret and state files are refused by name (both the requested name and the symlink-resolved name): dotfiles such as `.env`, `*.env`, `agents_llm_config*`, `*.pem`, `*.key`, `*.db`/`*.sqlite*` and the Hercules session/selector stores. The filesystem root and the home directory are never accepted as allowed roots. `run_hurl_suite` only runs `.hurl` files (a single file must have the `.hurl` extension; a directory runs its `*.hurl` files).

## Behaviour notes

- **`generic_http_api` body mode.** `body_mode` names are case-insensitive (`json`, `JSON`). Without a `body_mode`, a `dict`/`list`/number/boolean body is sent as JSON, a `str` body as `raw` (set the `Content-Type` header yourself) and `bytes` as `binary`. An unknown mode (`xml`, ...) returns an error listing the valid modes (`multipart`, `urlencoded`, `raw`, `binary`, `json`) instead of silently sending no body. With no body, the mode is ignored.
- **`generic_http_api` auth.** `auth_value` accepts `${name}` placeholders like every other argument. `auth_type` must be one of `basic`, `jwt`, `form_login`, `bearer`, `api_key`; an unknown type, a missing `auth_value`, or a `basic` value that is not `[username, password]` returns an error and sends nothing.
- **`generic_http_api` list query values.** A list value in `query_params` is sent as repeated keys (`{"k": ["a","b"]}` becomes `k=a&k=b`).
- **`generic_http_api` JSON body.** With `body_mode="json"` an empty body (`body={}`, `body=[]`, `body=0`) is now sent as JSON (`{}`); earlier revisions sent no body at all for falsy values. Only `body=None` sends no body.
- **`generic_http_api` query string.** A query string written in the URL is merged with `query_params`; repeated keys in the URL (`?tag=a&tag=b`) are kept, and a key given in `query_params` replaces every URL occurrence of that key.
- **`generic_http_api` result.** The tool returns one minified JSON string: `{"status_code":..,"status_type":..,"body":..,"duration_seconds":..}`. Earlier revisions of this tool returned a `(text, duration)` tuple, which the executor rendered as text; the duration is now a regular field and the whole result always parses as JSON. A response longer than 100,000 characters is shortened by truncating `body` (or `error_detail`) and setting `body_truncated` (or `error_detail_truncated`), so the result stays valid JSON. A result in that state cannot be asserted on: `assert_json_path`, `extract_from_response` and `validate_response_schema` return `ERROR` ("response was truncated") instead of a misleading `FAILED`/"matched nothing"; narrow the request (filters, pagination) and retry.
- **Captured variables** (`${name}`) are scoped to the scenario and cleared when the scenario starts, so a re-run never sees stale values.
- **`assert_json_path` with several matches.** `eq`, `ne`, `contains`, `gt`, `lt` and `not_empty` must hold for every match, and the failure names the first match that broke it. Use `exists`, `count_eq`, `all_gte`/`all_lte` or a narrower path for aggregate checks. `count_eq` needs a whole number.
- **Counting and bounds.** `count_eq`, `all_gte` and `all_lte` act on the elements of an array only when the path names exactly one value (no `[*]`, slice, filter, union or `..` anywhere): `$.body.items` with `count_eq 2` counts the array's elements. A multi-valued path counts or bounds its matches: `$.m[*]` on `[[1,2,3]]` has one match, so `count_eq 1` passes. A path that matches nothing has a `count_eq` count of 0 (`$.items[*]` on `[]` passes `count_eq 0`).
- **`expected` is required** for every operator except `exists`, `not_exists` and `not_empty`; leaving it out returns `ERROR` instead of silently comparing against null. Pass `null` explicitly to assert a null value.
- **Numbers.** `gt`, `lt`, `all_gte`, `all_lte` and `count_eq` accept numbers and plain decimal text (`"10"`, `"-1.5e2"`). Spellings such as `"1_000"`, `"inf"` or `"nan"` are not numeric and give `ERROR`. Whole numbers are compared as integers, so ids above 2^53 compare exactly.
- **`literal:` values** are compared exactly as written, including text that looks like a `${name}` placeholder: the prefix is removed first and the rest is never interpolated (`literal:${x}` asserts the six characters `${x}`).
- **Operator names in `expected`.** A value that is exactly an operator name, or an operator glued to a value (`count_eq:2`, `ne_path:$.x`), is rejected as a likely parameter mix-up. Ordinary strings that start with an operator word (`exists_in_cart`) are compared normally. To assert a value that is exactly an operator name, pass `literal:<value>`.
- **`run_hurl_suite`.** Variables are passed through `HURL_VARIABLE_<name>` environment entries, not `--variable` arguments (argv is visible to other local users). The raw output, and the JUnit report copy saved under `proofs/hurl_results/`, are scrubbed of secret-named variable values, the values of the scenario's secret-named captured variables (even when they are passed on under a harmless hurl-side name), credential headers (`Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` and any header whose name contains `token`, `secret`, `api-key` or `form-login`) and URL credentials before they are logged, saved or returned. Header values are redacted wherever they appear on a line: verbose `> Authorization: ...` / `* Cookie: ...` lines, quoted `--header '...'` arguments and mid-sentence mentions in failure messages. The verdict is `PASSED` only when the JUnit report has no failures **and** hurl exits with code 0.
- **`validate_against_openapi_spec` status lookup.** The response object is picked by the exact status code first, then the OpenAPI range key (`2XX`/`2xx` for a 2xx status, and so on), then `default`.
- **`assert_ui` text normalisation.** URLs and `id=`/`class=`/`data-testid=` assignments are stripped before matching. Pass `raw=true` to keep them (for example to assert that a link target is shown).

## Security notes

- **API tool logs are redacted.** `generic_http_api` request/response logs redact credential headers (`Authorization`, cookies, `X-Form-Login`, API-key and token headers), secret query parameters (names such as `token`, `api_key`, `client_secret`, `refresh_token`) and secret keys in JSON bodies at any nesting level up to eight levels deep. Error texts (which embed the request URL) have their URLs redacted before they are logged or returned.
- **Remote `$ref`s are blocked.** `validate_response_schema` and `validate_against_openapi_spec` resolve only in-document `#/...` references. An `http(s)://`, `file://` or relative-file `$ref` is refused with an error and is never fetched or read, so a schema or spec cannot be used for SSRF or local file reads.
- **File paths are confined** to the test data and project roots (plus `API_ALLOWED_PATHS`); see "Path confinement" above.
- **Subprocess environment.** `run_hurl_suite` starts hurl with a minimal environment (`PATH`, `HOME`, locale, temp dir, proxy and CA settings) plus the parent's own `HURL_VARIABLE_*` entries (hurl's documented way to inject variables, e.g. from CI) and the variables passed to the tool, which take precedence over a parent entry of the same name; the rest of the parent environment is not passed on. Header values and URL credentials in the nuclei command line are redacted in logs (the executed command is unchanged), and nuclei's stdout/stderr and result file get the same treatment (credential headers, the auth tokens passed to the tool, URL credentials) before they are logged or returned.
- **Known limitation: unverified nuclei download.** The nuclei binary is downloaded from GitHub releases without checksum or signature verification (pre-existing behaviour).

## Known limits

- **SSRF scope.** The URL guard (`utils/url_guard.py`) protects only the PRD fetch (`intelligence/prd_parser.py`). `generic_http_api`, `run_hurl_suite` and the nuclei security tools are **not** SSRF-filtered: they exist to test the application under test, which is routinely on `localhost` or a private address, so they request whatever URL the model supplies, internal and loopback addresses included. There is no egress allowlist; run Hercules on a network that restricts egress if that matters.
- **Redirects are never followed.** A 3xx answer comes back as an error object (`error`, `error_detail`, `status_code`, `status_type: "redirect"`), not as the redirect target's response.
- **No response headers.** `generic_http_api` returns the status code and body only; response headers (`Location`, `Set-Cookie`, rate-limit headers, ...) are neither returned nor assertable. They are only written, redacted, to the API log.
- **Non-2xx results carry `error_detail`, not `body`.** For a 4xx/5xx (or 3xx) response the payload is `{"error", "error_detail", "status_code", "status_type"}`; assert on `$.error_detail...`, not `$.body...`.
- **Oversized responses.** Anything over 100,000 characters is truncated (see above) and cannot be asserted on.
- **Schema `pattern` cost.** `validate_response_schema` evaluates schema `pattern` regexes with Python's `re` with no time limit, so a catastrophic-backtracking pattern in a model-written schema can stall the run. Python's regex engine cannot be interrupted from a thread, so no timeout is applied.
- **`format` is not enforced.** `format: email` / `date-time` / ... in a schema or OpenAPI spec is treated as an annotation (JSON Schema's default), not validated.
- **Security tool names.** The nuclei tool names are derived from the tag description and cut at 32 characters (for example `common_vulnerabilities_and_expos`); they are unique and stable, so look them up by the registered name.
