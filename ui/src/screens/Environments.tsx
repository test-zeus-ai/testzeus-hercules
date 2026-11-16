import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Field } from "../components/Field";
import { Icon } from "../components/Icon";
import { ApiError, api, post } from "../lib/api";
import { EMPTY_ENV, envToForm, environmentBody, targetSummary, validateEnvironment, type EnvForm } from "../lib/environmentForm";
import { BROWSER_CHOICES, browserOptionsBody, type BrowserOptionsForm } from "../lib/browserOptionsForm";
import type { Environment } from "../lib/types";

export { validateEnvironment };

export function EnvironmentsScreen() {
  const client = useQueryClient();
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => api<{ environments: Environment[] }>("/api/environments") });
  const devices = useQuery({ queryKey: ["devices"], queryFn: () => api<{ devices: string[] }>("/api/devices"), staleTime: Infinity });
  const [form, setForm] = useState<EnvForm>(EMPTY_ENV);
  const [editing, setEditing] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const errors = validateEnvironment(form);
  const save = useMutation({
    mutationFn: (overwrite: boolean) => post<Environment>("/api/environments", environmentBody(form, overwrite)),
    onSuccess: (env) => {
      void client.invalidateQueries({ queryKey: ["environments"] });
      setEditing(env.name);
      setTouched(false);
    },
  });
  // A save's outcome (a 409 "Replace it" in particular) belongs to the form it was made for:
  // editing the form or opening another environment drops it, so "Replace it" can never
  // overwrite with values the conflict was not about.
  const resetSave = save.reset;
  useEffect(() => resetSave(), [form, resetSave]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length === 0) save.mutate(editing === form.name);
  };
  const shown = touched ? errors : {};
  const saveError = save.error instanceof ApiError ? save.error : null;
  const list = envs.data?.environments ?? [];

  return (
    <section className="page" aria-labelledby="envs-title">
      <header className="page-head">
        <div>
          <h1 id="envs-title">Environments</h1>
          <p className="page-sub">Where cases run: a web app (on a desktop or an emulated phone) or a native app on a device, and the secrets they may use, by environment-variable name only.</p>
        </div>
        <button
          type="button"
          className="button"
          onClick={() => {
            setForm(EMPTY_ENV);
            setEditing(null);
            setTouched(false);
          }}
        >
          <Icon name="plus" size={13} /> New environment
        </button>
      </header>
      <div className="split">
        <div className="panel">
          {list.length === 0 ? (
            <p className="empty-note">No environments yet. Cases can also carry a base URL of their own.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Target</th>
                    <th scope="col">Viewport</th>
                    <th scope="col">Secrets</th>
                    <th scope="col">
                      <span className="visually-hidden">Edit</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((env) => (
                    <tr key={env.name} className={env.name === editing ? "row-selected" : undefined}>
                      <td className="mono nowrap">{env.name}</td>
                      <td className="mono">{targetSummary(env)}</td>
                      <td>{env.mobile ? "device" : (env.viewport ?? "–")}</td>
                      <td>
                        {env.secret_names.length ? (
                          env.secret_names.map((secret) => (
                            <span key={secret} className="tag mono">
                              <Icon name="key" size={11} /> {secret}
                            </span>
                          ))
                        ) : (
                          <span className="muted">none</span>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="button button-small button-ghost"
                          onClick={() => {
                            setForm(envToForm(env));
                            setEditing(env.name);
                            setTouched(false);
                          }}
                        >
                          Edit
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <form className="panel form" onSubmit={submit} noValidate aria-labelledby="env-form-title">
          <h2 id="env-form-title">{editing ? `Edit ${editing}` : "New environment"}</h2>
          <Field id="env-name" label="Name" error={shown.name}>
            <input className="mono" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="staging" />
          </Field>
          <Field id="env-target" label="Target">
            <select value={form.target} onChange={(event) => setForm({ ...form, target: event.target.value as EnvForm["target"] })}>
              <option value="web">Web app (browser)</option>
              <option value="app">Native app (Android or iOS device)</option>
            </select>
          </Field>
          <Field
            id="env-base-url"
            label="Base URL"
            error={shown.baseUrl}
            help={form.target === "app" ? "Optional: only API steps of the case use it." : undefined}
          >
            <input className="mono" value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} placeholder="http://localhost:3000" />
          </Field>
          {form.target === "web" ? (
            <>
              <Field id="env-device" label="Phone or tablet" help="Emulate a device in the browser (a Playwright device profile). Leave empty for a desktop browser.">
                <input className="mono" list="env-device-list" value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value })} placeholder="iPhone 15" />
              </Field>
              <datalist id="env-device-list">
                {(devices.data?.devices ?? []).map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <div className="field-row">
                <Field id="env-viewport" label="Viewport" error={shown.viewport} help={form.device ? "Empty: the device's own viewport." : undefined}>
                  <input className="mono" value={form.viewport} onChange={(event) => setForm({ ...form, viewport: event.target.value })} />
                </Field>
                <Field id="env-browser" label="Browser">
                  <select value={form.browser} onChange={(event) => setForm({ ...form, browser: event.target.value })}>
                    {BROWSER_CHOICES.map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <BrowserOptionsFields form={form} setForm={setForm} errors={shown} />
            </>
          ) : (
            <AppFields form={form} setForm={setForm} errors={shown} />
          )}
          <Field
            id="env-secrets"
            label="Secret references"
            error={shown.secrets}
            help="Names of environment variables in the daemon's environment. Values are read at run time and never stored or shown here."
          >
            <input className="mono" value={form.secrets} onChange={(event) => setForm({ ...form, secrets: event.target.value })} placeholder="SHOP_USER, SHOP_PASSWORD" />
          </Field>
          {saveError ? (
            <p className="callout callout-fail" role="alert">
              {saveError.message}
              {saveError.status === 409 ? (
                <button type="button" className="link-button" onClick={() => save.mutate(true)}>
                  Replace it
                </button>
              ) : null}
            </p>
          ) : null}
          {save.isSuccess && !touched ? <p className="notice" role="status">Saved .hercules/envs/{form.name}.json</p> : null}
          <footer className="form-actions">
            <button type="submit" className="button button-primary" disabled={save.isPending}>
              <Icon name="save" size={14} /> Save environment
            </button>
          </footer>
        </form>
      </div>
    </section>
  );
}

function BrowserOptionsFields({ form, setForm, errors }: { form: EnvForm; setForm: (form: EnvForm) => void; errors: Record<string, string> }) {
  const options = form.browserOptions;
  const update = (change: Partial<BrowserOptionsForm>) => setForm({ ...form, browserOptions: { ...options, ...change } });
  const count = Object.keys(browserOptionsBody(options) ?? {}).length;
  return (
    <details className="advanced env-browser-options" open={count > 0}>
      <summary>
        Browser options{count ? ` (${count} set)` : ""}
        <span className="muted"> · locale, time zone, signed-in state, headers, proxy and any other Playwright option</span>
      </summary>
      <div className="field-row">
        <Field id="env-bo-locale" label="Locale" error={errors["bo.locale"]}>
          <input className="mono" value={options.locale} onChange={(event) => update({ locale: event.target.value })} placeholder="en-GB" />
        </Field>
        <Field id="env-bo-timezone" label="Time zone" error={errors["bo.timezoneId"]}>
          <input className="mono" value={options.timezoneId} onChange={(event) => update({ timezoneId: event.target.value })} placeholder="Europe/Berlin" />
        </Field>
        <Field id="env-bo-color" label="Color scheme">
          <select value={options.colorScheme} onChange={(event) => update({ colorScheme: event.target.value as BrowserOptionsForm["colorScheme"] })}>
            <option value="">Browser default</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="no-preference">No preference</option>
          </select>
        </Field>
      </div>
      <Field
        id="env-bo-state"
        label="Signed-in state (auth JSON)"
        error={errors["bo.storageState"]}
        help="A file inside the project saved by Playwright's storage_state(): the run starts with its cookies and local storage. Keep it out of version control."
      >
        <input className="mono" value={options.storageState} onChange={(event) => update({ storageState: event.target.value })} placeholder=".hercules/auth/admin.json" />
      </Field>
      <Field id="env-bo-ua" label="User agent">
        <input className="mono" value={options.userAgent} onChange={(event) => update({ userAgent: event.target.value })} placeholder="Leave empty for the browser's own" />
      </Field>
      <Field id="env-bo-args" label="Launch switches" error={errors["bo.args"]} help="Browser command-line switches, separated by spaces.">
        <input className="mono" value={options.args} onChange={(event) => update({ args: event.target.value })} placeholder="--lang=de --disable-gpu" />
      </Field>
      <div className="field-row env-checks">
        <label className="field-check">
          <input type="checkbox" checked={options.ignoreHttpsErrors} onChange={(event) => update({ ignoreHttpsErrors: event.target.checked })} /> Accept invalid HTTPS certificates
        </label>
        <label className="field-check">
          <input type="checkbox" checked={options.headed} onChange={(event) => update({ headed: event.target.checked })} /> Show the browser window
        </label>
        <label className="field-check">
          <input type="checkbox" checked={options.recordVideo} onChange={(event) => update({ recordVideo: event.target.checked })} /> Record a video
        </label>
      </div>
      <Field
        id="env-bo-more"
        label="More Playwright options (JSON)"
        error={errors["bo.more"]}
        help='Any other launch or context option by its Python name, e.g. {"geolocation": {"latitude": 52.52, "longitude": 13.4}, "permissions": ["geolocation"], "extra_http_headers": {"Authorization": {"secret": "API_TOKEN"}}, "context_extra": {"strict_selectors": true}}. Secrets are {"secret": "ENV_VAR"}, never values.'
      >
        <textarea className="mono" rows={5} value={options.more} onChange={(event) => update({ more: event.target.value })} placeholder="{}" />
      </Field>
    </details>
  );
}

function AppFields({ form, setForm, errors }: { form: EnvForm; setForm: (form: EnvForm) => void; errors: Record<string, string> }) {
  const android = form.platform === "android";
  return (
    <>
      <div className="field-row">
        <Field id="env-platform" label="Platform">
          <select value={form.platform} onChange={(event) => setForm({ ...form, platform: event.target.value as EnvForm["platform"] })}>
            <option value="android">Android (UiAutomator2)</option>
            <option value="ios">iOS (XCUITest)</option>
          </select>
        </Field>
        <Field id="env-cloud" label="Runs on" error={errors.cloud}>
          <select value={form.cloud} onChange={(event) => setForm({ ...form, cloud: event.target.value as EnvForm["cloud"] })}>
            <option value="">Local Appium server</option>
            <option value="browserstack">BrowserStack</option>
            <option value="saucelabs">Sauce Labs</option>
          </select>
        </Field>
      </div>
      <Field id="env-app" label="App" error={errors.app} help="A file inside the project, a device cloud app id, or leave empty and name the installed app below.">
        <input className="mono" value={form.app} onChange={(event) => setForm({ ...form, app: event.target.value })} placeholder={android ? "apps/demo.apk" : "apps/Demo.app"} />
      </Field>
      {android ? (
        <div className="field-row">
          <Field id="env-package" label="Package" error={errors.appPackage}>
            <input className="mono" value={form.appPackage} onChange={(event) => setForm({ ...form, appPackage: event.target.value })} placeholder="com.android.settings" />
          </Field>
          <Field id="env-activity" label="Activity" error={errors.activity}>
            <input className="mono" value={form.activity} onChange={(event) => setForm({ ...form, activity: event.target.value })} placeholder=".Settings" />
          </Field>
        </div>
      ) : (
        <Field id="env-bundle-id" label="Bundle id" error={errors.bundleId}>
          <input className="mono" value={form.bundleId} onChange={(event) => setForm({ ...form, bundleId: event.target.value })} placeholder="com.apple.Preferences" />
        </Field>
      )}
      <div className="field-row">
        <Field id="env-app-device" label="Device">
          <input className="mono" value={form.appDevice} onChange={(event) => setForm({ ...form, appDevice: event.target.value })} placeholder={android ? "emulator-5554" : "iPhone 15"} />
        </Field>
        <Field id="env-platform-version" label="OS version" error={errors.platformVersion}>
          <input className="mono" value={form.platformVersion} onChange={(event) => setForm({ ...form, platformVersion: event.target.value })} placeholder={android ? "14" : "17.5"} />
        </Field>
      </div>
      {form.cloud ? (
        <p className="field-help">The {form.cloud === "browserstack" ? "BrowserStack" : "Sauce Labs"} user name and access key are read from the environment variables named in the project's config (mobile.{form.cloud}); a device cloud needs an egress policy other than local.</p>
      ) : (
        <Field id="env-appium-url" label="Appium server" error={errors.appiumUrl} help="Empty: the project's default (http://127.0.0.1:4723).">
          <input className="mono" value={form.appiumUrl} onChange={(event) => setForm({ ...form, appiumUrl: event.target.value })} placeholder="http://127.0.0.1:4723" />
        </Field>
      )}
    </>
  );
}
