import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Icon, type IconName } from "../components/Icon";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { docsUrl } from "../lib/links";
import { useFont, type FontChoice } from "../lib/font";
import { deliverSample, enableDesktop } from "../lib/notify/deliver";
import { desktopPermission, useNotifyPrefs, type NotifyPrefs } from "../lib/notify/prefs";
import { useTheme, type ThemeChoice } from "../lib/theme";
import type { Health } from "../lib/types";

const DOCS: { page: string; title: string; about: string }[] = [
  { page: "index.md", title: "User guide", about: "Every page, the terms used, and the commands at a glance" },
  { page: "tester-ui-quick-start.md", title: "Quick start: the tester UI", about: "From an empty project to a passing case in the browser" },
  { page: "gherkin.md", title: "Writing Gherkin", about: "Quoting, @env:, test data, checks, files, and the editor's keys" },
  { page: "writing-tests.md", title: "Writing tests", about: "The case format, steps, expected outcomes, test data and secrets" },
  { page: "running-tests.md", title: "Running tests", about: "Runs, results and evidence, debugging and approvals" },
  { page: "explore.md", title: "Exploratory testing", about: "Explore sessions, findings and triage" },
  { page: "modes-and-privacy.md", title: "Modes and privacy", about: "Pure and hybrid mode, the egress policy, telemetry" },
  { page: "troubleshooting.md", title: "Troubleshooting", about: "Doctor checks, model servers, browsers and the daemon" },
];

const FONTS: { value: FontChoice; label: string; family: string; help: string }[] = [
  { value: "manrope", label: "Manrope", family: '"Manrope", sans-serif', help: "Hercules' own font." },
  { value: "inter", label: "Inter", family: '"Inter", sans-serif', help: "A plain, very legible screen font." },
  { value: "atkinson", label: "Atkinson Hyperlegible", family: '"Atkinson Hyperlegible", sans-serif', help: "Made by the Braille Institute for low vision: letters that are easy to tell apart." },
  { value: "system", label: "System", family: "system-ui, sans-serif", help: "This device's own font." },
];

const THEMES: { value: ThemeChoice; label: string; icon: IconName; help: string }[] = [
  { value: "system", label: "System", icon: "auto", help: "Follows this device's light or dark setting." },
  { value: "light", label: "Light", icon: "sun", help: "Always light." },
  { value: "dark", label: "Dark", icon: "moon", help: "Always dark." },
];

const RUN_NOTICES: { value: NotifyPrefs["runs"]; label: string; help: string }[] = [
  { value: "all", label: "Every run", help: "A notification when any run ends: passed, failed, errored or cancelled. A folder run is one notification." },
  { value: "failures", label: "Failures only", help: "Only runs that fail or stop with an error." },
  { value: "off", label: "Off", help: "No notification when a run ends." },
];

function Toggle({ id, label, help, checked, onChange }: { id: string; label: string; help: ReactNode; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <div className="check-option">
      <input id={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} aria-describedby={`${id}-help`} />
      <label htmlFor={id}>{label}</label>
      <span className="field-help small" id={`${id}-help`}>
        {help}
      </span>
    </div>
  );
}

function NotificationsCard() {
  const [prefs, setPrefs] = useNotifyPrefs();
  const [permission, setPermission] = useState(desktopPermission);
  const chosen = RUN_NOTICES.find((item) => item.value === prefs.runs) ?? RUN_NOTICES[0];
  const allow = async () => setPermission(await enableDesktop());

  return (
    <section className="panel card card-wide" aria-labelledby="notifications-title">
      <h2 id="notifications-title">Notifications</h2>
      <div className="settings-group">
        <h3 id="notify-runs-title">When a run ends</h3>
        <div className="segmented" role="radiogroup" aria-labelledby="notify-runs-title">
          {RUN_NOTICES.map((item) => (
            <button key={item.value} type="button" role="radio" aria-checked={prefs.runs === item.value} className={`segment${prefs.runs === item.value ? " segment-on" : ""}`} onClick={() => setPrefs({ runs: item.value })}>
              {item.label}
            </button>
          ))}
        </div>
        <p className="small muted settings-note">{chosen.help}</p>
      </div>
      <div className="settings-group check-options">
        <Toggle id="notify-approvals" label="Runs waiting for you" help="A step or a plan to approve. The notification stays until the run moves on." checked={prefs.approvals} onChange={(approvals) => setPrefs({ approvals })} />
        <Toggle id="notify-explore" label="Explore sessions" help="When a session finishes, fails or is stopped, with its findings count." checked={prefs.explore} onChange={(explore) => setPrefs({ explore })} />
        <Toggle id="notify-sound" label="Sound" help="A short chime with each notification." checked={prefs.sound} onChange={(sound) => setPrefs({ sound })} />
        {permission === "granted" ? (
          <Toggle id="notify-desktop" label="Desktop notifications" help="From your system while this page is in the background or another app is in front." checked={prefs.desktop} onChange={(desktop) => setPrefs({ desktop })} />
        ) : null}
      </div>
      {permission === "default" ? (
        <div className="settings-group">
          <h3>Desktop notifications</h3>
          <p className="small muted">From your system while this page is in the background or another app is in front. The browser asks you first.</p>
          <button type="button" className="button button-small" onClick={() => void allow()}>
            Allow desktop notifications
          </button>
        </div>
      ) : null}
      {permission === "denied" ? (
        <p className="small muted settings-note">This browser blocks notifications from this page. Allow them in its site settings (the icon left of the address), then reload.</p>
      ) : null}
      {permission === "unsupported" ? (
        <p className="small muted settings-note">Desktop notifications need the UI opened at 127.0.0.1 or localhost, in a browser that has them. Toasts on this page still work.</p>
      ) : null}
      <div className="settings-note">
        <button type="button" className="button button-small button-ghost" onClick={() => deliverSample(prefs)}>
          Send a test notification
        </button>
      </div>
      <p className="small muted settings-note">Remembered in this browser. Runs from <code>hercules run</code> in a terminal do not go through the UI, so they do not notify here.</p>
    </section>
  );
}

function Detail({ label, value, mono = true }: { label: string; value: string | number | null | undefined; mono?: boolean }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value === null || value === undefined || value === "" ? <span className="muted">unknown</span> : value}</dd>
    </>
  );
}

export function SettingsScreen() {
  const [theme, , setTheme] = useTheme();
  const [font, setFont] = useFont();
  const chosenFont = FONTS.find((item) => item.value === font) ?? FONTS[0];
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/api/health") });
  const info = health.data;
  const chosen = THEMES.find((item) => item.value === theme) ?? THEMES[0];

  return (
    <section className="page" aria-labelledby="settings-title">
      <header className="page-head">
        <div>
          <h1 id="settings-title">Settings</h1>
          <p className="page-sub">How this tester UI looks and notifies you, and who and where Hercules is running.</p>
        </div>
      </header>
      <div className="cards settings-cards">
        <section className="panel card" aria-labelledby="appearance-title">
          <h2 id="appearance-title">Appearance</h2>
          <div className="settings-group">
            <h3 id="theme-title">Theme</h3>
            <div className="segmented" role="radiogroup" aria-labelledby="theme-title">
              {THEMES.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  role="radio"
                  aria-checked={theme === item.value}
                  className={`segment${theme === item.value ? " segment-on" : ""}`}
                  onClick={() => setTheme(item.value)}
                >
                  <Icon name={item.icon} size={14} /> {item.label}
                </button>
              ))}
            </div>
            <p className="small muted settings-note">{chosen.help}</p>
          </div>
          <div className="settings-group">
            <h3 id="font-title">Font</h3>
            <div className="segmented" role="radiogroup" aria-labelledby="font-title">
              {FONTS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  role="radio"
                  aria-checked={font === item.value}
                  className={`segment${font === item.value ? " segment-on" : ""}`}
                  style={{ fontFamily: item.family }}
                  onClick={() => setFont(item.value)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <p className="small muted settings-note">{chosenFont.help} Code and file names keep JetBrains Mono.</p>
          </div>
        </section>

        <section className="panel card" aria-labelledby="details-title">
          <h2 id="details-title">Details</h2>
          {health.isError ? <p className="callout callout-fail">Could not reach the daemon: {(health.error as Error).message}</p> : null}
          {!info && !health.isError ? <p className="muted">Loading…</p> : null}
          {info ? (
            <dl className="kv">
              <Detail label="User" value={info.user} />
              <Detail label="Computer" value={info.host} />
              <Detail label="Project" value={info.project_dir} />
              <Detail label="Hercules home" value={info.home_dir} />
              <Detail label="Version" value={info.version} />
              <Detail label="Daemon" value={info.ui_url} />
              <Detail label="Started" value={info.started_at ? formatDateTime(info.started_at) : null} mono={false} />
              <Detail label="Process" value={info.pid} />
              <Detail label="Parallel runs" value={info.parallel_runs} />
              <Detail label="Egress policy" value={info.egress_policy} />
            </dl>
          ) : null}
          <p className="small muted settings-note">Read from the running daemon (hercules serve). Change them in its config and restart it.</p>
        </section>

        <NotificationsCard />

        <section className="panel card card-wide" aria-labelledby="docs-title">
          <h2 id="docs-title">Docs</h2>
          <ul className="docs-list">
            {DOCS.map((doc) => (
              <li key={doc.page}>
                <a href={docsUrl(doc.page)} target="_blank" rel="noopener noreferrer">
                  {doc.title}
                  <span className="visually-hidden"> (new tab)</span>
                </a>
                <span className="small muted">{doc.about}</span>
              </li>
            ))}
          </ul>
          <p className="small muted settings-note">The user guide on GitHub; the same pages are in this project's docs/guide folder.</p>
        </section>
      </div>
    </section>
  );
}
