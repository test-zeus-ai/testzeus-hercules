# Security scans

> **Watch it:** [Security scans (1:18)](videos.md#13-security-scans).

Hercules can scan your app for known security issues with
[nuclei](https://github.com/projectdiscovery/nuclei), an open-source scanner. You can run
a scan from the command line (`hercules scan`) or as a step of a test case. The scan only
targets the app under test: the case's base URL, or the operations of an OpenAPI spec in
your project.

Only scan apps you own or are allowed to test.

## Install the scanner

nuclei is a separate program. Hercules does not download it on its own.

1. Install it with your package manager, for example `brew install nuclei`.
   Or run `hercules scan --install`: it downloads the nuclei release pinned in Hercules
   (3.11.1) for your system (Linux, macOS on Intel or Apple silicon, Windows), checks its
   SHA-256 checksum, and puts it in the Hercules cache (`~/.hercules/cache/nuclei/`).
2. Download the scanner's templates: `hercules scan --update-templates` (this runs
   `nuclei -update-templates`). Run it again now and then to get new templates.
3. Check with `hercules doctor`: the `scanner` line shows the version and the path.

Hercules looks for the scanner in this order: the `HERCULES_NUCLEI` variable (a path to
the program), `nuclei` on your `PATH`, then the copy in the Hercules cache.

## Scan from the command line

```bash
hercules scan --base-url http://localhost:8000
hercules scan --env staging --tags cve,exposure,misconfig --fail-on high
hercules scan --base-url http://localhost:8001 --openapi specs/pets.yaml --tags exposure
hercules scan --env staging --header 'Authorization: Bearer {{API_TOKEN}}'
```

| Option | Meaning |
|---|---|
| `--base-url URL`, `--env NAME` | The app to scan: a URL, or the base URL of a saved environment |
| `--path PATH` | Scan a path under the base URL instead, such as `/api` |
| `--openapi SPEC` | Scan the operations of an OpenAPI (or Swagger) spec in the project |
| `--tags TAGS` | Template tags to run, comma-separated. Default: `cve,exposure,misconfig` |
| `--exclude-tags TAGS` | Template tags to leave out |
| `--fail-on SEVERITY` | Exit 1 when a finding is at or above `critical`, `high`, `medium` (default), `low` or `info` |
| `--allow-intrusive` | Also run intrusive, denial-of-service and fuzzing templates (see [Safety](#safety)) |
| `--rate-limit N` | Most requests per second (default 50) |
| `--timeout-s S` | Time limit of the scan (default 600 s) |
| `--header 'NAME: VALUE'` | A header for every request; `{{NAME}}` in the value reads the environment variable `NAME` |
| `--json` | Print the result as JSON |

Exit codes: 0 when no finding reaches `--fail-on`, 1 when one does, 2 when the scan could
not run (no scanner, a refused target, the scanner failed or ran out of time).

Each scan writes its evidence to `.hercules/scans/<id>/`: `summary.txt` (the findings for a
person), `findings.jsonl` (one finding per line) and `scan.json` (what ran, the scanner
version, counts, the end of the scanner's output).

Common tags: `cve` (known vulnerabilities), `exposure` (exposed files and data),
`misconfig` (misconfigurations), `panel` (admin panels), `tech` (technology detection),
`xss`, `sqli`, `lfi`, `rce`, `ssrf`, `redirect`, `takeover`, `wordpress`, `unauth`. The
scanner's own list: `nuclei -tgl`.

## Scan in a test case

A step with a `security` payload runs a scan. Like API steps, it needs no browser: a
case made only of scan and API steps never opens one.

```json
{
  "id": "shop-security",
  "title": "The shop has no serious security findings",
  "environment": {"ref": "staging"},
  "timeout_s": 600,
  "test_data": {"token": {"secret": "SHOP_API_TOKEN"}},
  "steps": [
    {
      "action": "Scan the shop for known issues",
      "security": {"tags": ["cve", "exposure", "misconfig"], "fail_on": "high"}
    },
    {
      "action": "Scan the API with a token",
      "security": {
        "openapi": "specs/shop.yaml",
        "tags": ["exposure"],
        "headers": {"Authorization": "Bearer {{token}}"}
      }
    }
  ]
}
```

| Field | Default | Meaning |
|---|---|---|
| `tags` | `cve`, `exposure`, `misconfig` | Template tags to run |
| `exclude_tags` | none | Template tags to leave out |
| `path` | none | A path under the base URL to scan instead of the base URL |
| `openapi` | none | A spec file in the project whose operations are scanned (sent to the base URL) |
| `headers` | none | Headers for every request; use `{{name}}` placeholders for secrets |
| `auth` | none | `{"type": "bearer", "value": "{{token}}"}` or `{"type": "basic", "value": "user:{{password}}"}`, as in API steps |
| `fail_on` | `medium` | Findings at or above this severity fail the step |
| `intrusive` | `false` | Also run intrusive, denial-of-service and fuzzing templates |
| `rate_limit` | 50 | Most requests per second |
| `timeout_s` | 240 | Time limit of the scan in seconds |

The case's `timeout_s` also limits the run (300 s by default). Raise it for cases with
long scans.

### Results

- A finding at or above `fail_on` fails the step. The step's `actual` lists those
  findings, most severe first, and the failure is classified as a product bug.
- A scan that cannot run (no scanner, a target outside the app, missing templates, out
  of time) makes the step an error with the reason.
- Each scan step's evidence is in `steps/<n>/` of the run: `summary.txt`,
  `findings.jsonl` and `scan.json`.
- `run.json` has a `security` section: every scan with its findings and the counts per
  severity. `hercules run` prints a `Security:` line.
- `junit.xml` has the counts as suite properties (`security_high`, ...) and the failing
  findings in the failed step and in `system-out`.
- The tester UI shows a "Security scans" panel on the run page, with the failing
  findings first and the others folded by template.
- `hercules export` writes a scan step as a comment with the matching `hercules scan`
  command, to run in your pipeline.

### In a `.feature` file

A step that asks for a security scan in plain words becomes a scan step, without a model:

```gherkin
Feature: Shop security
  Scenario: The shop has no serious findings
    When I run a security scan for "cve, exposure, misconfig"
    Then there should be no security findings at or above "high"
```

The scan step understands tags in quotes after `for` (or tag words such as "SQL injection"
and "cross-site scripting"), `of the OpenAPI spec "specs/shop.yaml"`, `on the path "/api"`,
`failing on "high"` and `at 10 requests per second`. A `Then` right after it that says there are no findings becomes the
step's expected outcome, and its severity the step's `fail_on`. Words never turn on
intrusive templates: set `intrusive: true` in the case.

## Safety

- **Only the app under test.** A scan targets the base URL (or a path under it). A URL on
  another scheme, host or port is refused. A spec must be a file inside the project; a
  path outside it, `..`, and secret or state files (`.env`, keys, databases) are refused.
  With a base URL, the spec's operations are sent to the base URL, whatever servers the
  spec lists.
- **Gentle by default.** Templates tagged `intrusive`, `dos`, `fuzz`, `fuzzing`, `dast`,
  `bruteforce` and `default-login` are left out. To run them, set `intrusive: true` (or
  `--allow-intrusive`), and only on an app you may break: they send attack payloads that
  can change data, or try default passwords that can lock accounts. With it, an OpenAPI
  scan also fuzzes the parameters of each operation.
- **The app's host.** Some templates check a product on its own well-known port, so a
  scan may also knock on other ports of the app's host. It never scans another host.
- **No silent passes.** When the app stops answering during a scan (the scanner gives up
  on it after repeated errors), the scan is incomplete and the step is an error, not a
  pass. Lower `rate_limit` for small or rate-limited servers.
- **Nothing else leaves your machine.** The scanner runs without its update check and
  without out-of-band callbacks to public interaction servers (templates that need them
  are skipped). Only HTTP and TLS templates run. The request rate is limited.
- **Secrets stay hidden.** Header values and URL credentials are masked in the command
  shown, in the scanner's output, in the findings and in every evidence file. The
  scanner gets the real values.

## How an OpenAPI scan works

By default, the scan runs the chosen templates against the API's URLs: the server URL and
every path of the spec, path parameters filled with their example (or `1`). With
`intrusive: true`, the scanner reads the spec and fuzzes each operation's parameters with
its DAST templates.

## From v1

The v1 security tools are replaced by `hercules scan` and scan steps.
`hercules import v1` turns a v1 security feature (its name or tags say "security") into
scan cases: each scenario's topic (CVEs, sensitive data exposure, XSS, SQL injection,
misconfigurations, ...) becomes the scan's tag, its `Then` the expected outcome, and the
"base URL" step is dropped (the imported environment holds the base URL). A scenario about
intrusive testing is not converted; add `intrusive: true` by hand if you want it.
