# Browsers and browser options

> **Watch it:** [Browsers and browser options (1:22)](videos.md#7-browsers-and-browser-options).

A web case runs in the browser its environment names, set up with Playwright's own launch
and context options: the locale and time zone, a signed-in state, extra headers, a proxy,
and any other option Playwright has. The same browser and options are used by local runs,
by the code `hercules export` writes, and by runs on a cloud.

## The browser

| `browser` | What runs | Install |
|---|---|---|
| `chromium` (default) | Playwright's Chromium, or the one in `HERCULES_CHROMIUM` | `python -m playwright install chromium` |
| `chrome` | The Google Chrome installed on the machine | `python -m playwright install chrome`, or install Chrome |
| `msedge` | The Microsoft Edge installed on the machine | `python -m playwright install msedge`, or install Edge |
| `firefox` | Playwright's Firefox | `python -m playwright install firefox` |
| `webkit` | Playwright's WebKit (Safari's engine) | `python -m playwright install webkit` |

`uvx testzeus-hercules` asks which of them to set up (Chromium by default, plus any browser
your saved environments use), or takes `--browsers chromium,firefox,webkit`. On Linux,
add `--with-deps` to `playwright install` to get their system libraries too.

Firefox has no phone or tablet mode, so a `device` profile (such as `iPhone 15`) needs
Chromium, Chrome, Edge or WebKit. The live view and **take over** work in every browser:
Chromium streams the page, Firefox and WebKit send screenshots several times a second.

## Browser options

`browser_options` holds Playwright's options by their Python names. Set them on a saved
environment (the **Browser options** section of the tester UI's environment form) or in a
case's `environment`; a case's own options win over its environment's, one by one.

```json
{
  "name": "staging-de",
  "base_url": "https://staging.example.com",
  "browser": "firefox",
  "browser_options": {
    "locale": "de-DE",
    "timezone_id": "Europe/Berlin",
    "color_scheme": "dark",
    "storage_state": ".hercules/auth/admin.json",
    "extra_http_headers": {"X-Tenant": "acme", "Authorization": {"secret": "STAGING_TOKEN"}},
    "http_credentials": {"username": "qa", "password": {"secret": "STAGING_BASIC_PASSWORD"}},
    "ignore_https_errors": true,
    "geolocation": {"latitude": 52.52, "longitude": 13.405},
    "permissions": ["geolocation"],
    "record_video": true,
    "context_extra": {"screen": {"width": 1920, "height": 1080}, "strict_selectors": true},
    "launch_extra": {"timeout": 60000}
  },
  "secret_names": ["STAGING_TOKEN", "STAGING_BASIC_PASSWORD"]
}
```

| Option | Meaning |
|---|---|
| `headless` | `false` opens a visible window (`hercules run --headed` always does) |
| `slow_mo` | Milliseconds Playwright waits after each action |
| `args` | Browser command-line switches, such as `--lang=de` |
| `firefox_user_prefs` | Firefox `about:config` preferences (Firefox only) |
| `proxy` | `{"server": "http://proxy:3128", "bypass": ".internal", "username": ..., "password": {"secret": ...}}` |
| `locale`, `timezone_id` | Language and time zone the pages see (`de-DE`, `Europe/Berlin`) |
| `geolocation`, `permissions` | A position, and the permissions granted to every page |
| `color_scheme`, `reduced_motion`, `forced_colors` | Media features the pages see |
| `user_agent` | Replaces the browser's user agent |
| `extra_http_headers` | Headers sent with every request |
| `http_credentials` | HTTP basic or digest authentication |
| `ignore_https_errors` | Accept self-signed and invalid certificates |
| `java_script_enabled`, `bypass_csp`, `offline`, `accept_downloads`, `service_workers` | As in Playwright |
| `device_scale_factor`, `is_mobile`, `has_touch` | Screen density and touch (`is_mobile`: not in Firefox) |
| `storage_state` | The signed-in state: a JSON file inside the project (cookies and local storage) |
| `record_video` | Record a video into the run's evidence folder (`video/`) |
| `launch_extra` | Any other `BrowserType.launch` option, by its Python name |
| `context_extra` | Any other `Browser.new_context` option, by its Python name |

Names in `launch_extra` and `context_extra` are checked against the installed
Playwright's own parameters, so a typo is refused with the closest name. Options that
write files anywhere or run other programs (`executable_path`, `downloads_path`,
`record_har_path`, `traces_dir`, ...) are not accepted; `record_video` keeps the video with
the run.

### Secrets

A header value, the HTTP credentials and the proxy password can be `{"secret": "NAME"}`:
the value is read from that environment variable when the browser starts, and is never
written to the environment file, the run's evidence or the exported code (which reads the
same variable). A missing variable stops the run before the browser starts, naming the
variable.

### Signed-in state (auth JSON)

`storage_state` names a JSON file inside the project, as Playwright's
`context.storage_state(path=...)` saves it. The run starts with its cookies and local
storage, so a case can begin past the sign-in page. Save one with a few lines of
Playwright:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto("https://staging.example.com/login")
    input("Sign in in the browser window, then press Enter here ")
    page.context.storage_state(path=".hercules/auth/admin.json")
```

The file holds a live session: keep it out of version control and refresh it when the
session expires.

## In exported code

`hercules export` writes the run's browser and options into the test:

- **pytest-playwright**: a `browser_type_launch_args` fixture with the launch options, a
  `browser_context_args` fixture with the context options, and for Firefox or WebKit
  `pytestmark = pytest.mark.only_browser("firefox")`. Run it with `pytest --browser firefox`.
- **Playwright Test**: `test.use({ browserName, channel, launchOptions })` and a
  `test.use({...})` with the context options (`contextOptions` for the rest).

Secrets are read from the same environment variables, and the auth file from
`STORAGE_STATE` when it is set, else its path in the project. `hercules export --check`
runs the test in the run's browser.

## On a cloud

A run on BrowserStack, Sauce Labs or TestMu AI uses the run's browser unless you pick
another: a Chromium run goes to Chrome by default (or Edge or Chromium), a Firefox or
WebKit run to the cloud's Playwright Firefox or WebKit. The cloud project includes the auth
file at the same path, and the options travel in the exported test. See
[Running tests on clouds](clouds.md).
