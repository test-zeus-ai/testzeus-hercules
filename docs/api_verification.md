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

Every assertion verdict (passes included) is appended to `proofs/assertion_logs.log`. Values in that ledger are redacted: a secret-named path or capture name (`password`, `token`, `secret`, `api_key`, ...) hides the value, and the values of secret-named captured variables are scrubbed from the text.

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

- **`generic_http_api` JSON body.** With `body_mode="json"` an empty body (`body={}`, `body=[]`, `body=0`) is now sent as JSON (`{}`); earlier revisions sent no body at all for falsy values. Only `body=None` sends no body.
- **`generic_http_api` query string.** A query string written in the URL is merged with `query_params`; repeated keys in the URL (`?tag=a&tag=b`) are kept, and a key given in `query_params` replaces every URL occurrence of that key.
- **`generic_http_api` result.** The tool returns one minified JSON string: `{"status_code":..,"status_type":..,"body":..,"duration_seconds":..}`. Earlier revisions of this tool returned a `(text, duration)` tuple, which the executor rendered as text; the duration is now a regular field and the whole result always parses as JSON. A response longer than 100,000 characters is shortened by truncating `body` (or `error_detail`) and setting `body_truncated` (or `error_detail_truncated`), so the result stays valid JSON.
- **Captured variables** (`${name}`) are scoped to the scenario and cleared when the scenario starts, so a re-run never sees stale values.
- **`assert_json_path` with several matches.** `eq`, `ne`, `contains`, `gt`, `lt` and `not_empty` must hold for every match, and the failure names the first match that broke it. Use `exists`, `count_eq`, `all_gte`/`all_lte` or a narrower path for aggregate checks. `count_eq` needs a whole number.
- **Operator names in `expected`.** A value that is exactly an operator name, or an operator glued to a value (`count_eq:2`, `ne_path:$.x`), is rejected as a likely parameter mix-up. Ordinary strings that start with an operator word (`exists_in_cart`) are compared normally. To assert a value that is exactly an operator name, pass `literal:<value>`.
- **`run_hurl_suite`.** Variables are passed through `HURL_VARIABLE_<name>` environment entries, not `--variable` arguments (argv is visible to other local users). The raw output, and the JUnit report copy saved under `proofs/hurl_results/`, are scrubbed of secret-named variable values, the values of the scenario's secret-named captured variables (even when they are passed on under a harmless hurl-side name), `Authorization`/cookie header lines and URL credentials before they are logged, saved or returned. The verdict is `PASSED` only when the JUnit report has no failures **and** hurl exits with code 0.
- **`assert_ui` text normalisation.** URLs and `id=`/`class=`/`data-testid=` assignments are stripped before matching. Pass `raw=true` to keep them (for example to assert that a link target is shown).

## Security notes

- **API tool logs are redacted.** `generic_http_api` request/response logs redact credential headers (`Authorization`, cookies, `X-Form-Login`, API-key and token headers), secret query parameters (names such as `token`, `api_key`, `client_secret`, `refresh_token`) and secret keys in JSON bodies at any nesting level up to eight levels deep. Error texts (which embed the request URL) have their URLs redacted before they are logged or returned.
- **Remote `$ref`s are blocked.** `validate_response_schema` and `validate_against_openapi_spec` resolve only in-document `#/...` references. An `http(s)://`, `file://` or relative-file `$ref` is refused with an error and is never fetched or read, so a schema or spec cannot be used for SSRF or local file reads.
- **File paths are confined** to the test data and project roots (plus `API_ALLOWED_PATHS`); see "Path confinement" above.
- **Subprocess environment.** `run_hurl_suite` starts hurl with a minimal environment (`PATH`, `HOME`, locale, temp dir, proxy and CA settings) plus the parent's own `HURL_VARIABLE_*` entries (hurl's documented way to inject variables, e.g. from CI) and the variables passed to the tool, which take precedence over a parent entry of the same name; the rest of the parent environment is not passed on. Header values and URL credentials in the nuclei command line are redacted in logs; the executed command is unchanged.
- **Known limitation: no egress allowlist.** `generic_http_api` can request any URL the model supplies, including internal or loopback addresses. Run Hercules on a network that restricts egress if that matters.
- **Known limitation: unverified nuclei download.** The nuclei binary is downloaded from GitHub releases without checksum or signature verification (pre-existing behaviour).
