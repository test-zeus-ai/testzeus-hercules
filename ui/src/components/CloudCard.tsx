import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { post, put, remove } from "../lib/api";
import { cloudMonogram, connectionLabel } from "../lib/clouds";
import type { Cloud, CloudCheck, CloudRegionSaved, CloudRemoved, CloudSaved } from "../lib/types";
import { CloudLogo } from "./CloudLogo";
import { Field } from "./Field";
import { Icon } from "./Icon";

/** A cloud's logo on a square tile; its monogram when Hercules has no logo for it. Decorative: the name is shown beside it. */
export function CloudMark({ cloud, name, small = false }: { cloud: string; name: string; small?: boolean }) {
  const logo = CloudLogo({ cloud });
  return (
    <span className={`cloud-mark${logo ? " cloud-mark-logo" : ""}${small ? " cloud-mark-small" : ""}`} aria-hidden="true">
      {logo ?? cloudMonogram(name)}
    </span>
  );
}

type Outcome = { tone: "ok" | "fail" | "note"; text: string };

function Readiness({ label, reason }: { label: string; reason: string | null }) {
  return (
    <div className={`cloud-ready-item${reason ? " cloud-ready-no" : ""}`}>
      <dt>{label}</dt>
      <dd>
        {reason ? (
          <>
            <span className="text-fail">
              <Icon name="cross" size={12} /> not ready
            </span>
            <span className="cloud-ready-reason">{reason}</span>
          </>
        ) : (
          <span className="text-pass">
            <Icon name="check" size={12} /> ready
          </span>
        )}
      </dd>
    </div>
  );
}

function ConnectForm({ cloud, onOutcome }: { cloud: Cloud; onOutcome: (outcomes: Outcome[]) => void }) {
  const client = useQueryClient();
  const [username, setUsername] = useState("");
  const [accessKey, setAccessKey] = useState("");
  const [touched, setTouched] = useState(false);
  const save = useMutation({
    mutationFn: () => put<CloudSaved>(`/api/clouds/${cloud.id}`, { username: username.trim(), access_key: accessKey }),
    onSuccess: (saved) => {
      // The key goes to the daemon once and is never kept in the page.
      setAccessKey("");
      setUsername("");
      setTouched(false);
      onOutcome([{ tone: saved.check.ok ? "ok" : "fail", text: saved.check.ok ? `Saved. ${saved.check.message}` : `Saved, but the check failed: ${saved.check.message}` }, ...(saved.note ? [{ tone: "note" as const, text: saved.note }] : [])]);
      void client.invalidateQueries({ queryKey: ["clouds"] });
    },
  });
  const errors = { username: username.trim() ? undefined : "Enter the user name.", accessKey: accessKey ? undefined : "Paste the access key." };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!errors.username && !errors.accessKey) save.mutate();
  };
  return (
    <form className="form cloud-form" onSubmit={submit} noValidate aria-label={`Connect ${cloud.name}`}>
      <div className="field-row">
        <Field id={`cloud-${cloud.id}-username`} label="User name" error={touched ? errors.username : undefined} required>
          <input className="mono" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" spellCheck={false} autoCapitalize="off" placeholder={cloud.username ?? ""} />
        </Field>
        <Field id={`cloud-${cloud.id}-key`} label="Access key" error={touched ? errors.accessKey : undefined} required>
          <input className="mono" type="password" value={accessKey} onChange={(event) => setAccessKey(event.target.value)} autoComplete="off" spellCheck={false} autoCapitalize="off" />
        </Field>
      </div>
      <p className="cloud-form-links small">
        <a href={cloud.keys_url} target="_blank" rel="noopener noreferrer">
          Where do I find my key? <Icon name="link" size={12} />
        </a>
        <a href={cloud.signup_url} target="_blank" rel="noopener noreferrer">
          No account? Sign up <Icon name="link" size={12} />
        </a>
      </p>
      {save.error ? (
        <p className="callout callout-fail" role="alert">
          {(save.error as Error).message}
        </p>
      ) : null}
      <footer className="form-actions">
        <button type="submit" className="button button-primary" disabled={save.isPending}>
          <Icon name="plug" size={14} /> {save.isPending ? "Connecting…" : cloud.connected ? "Save new keys" : `Connect ${cloud.name}`}
        </button>
      </footer>
    </form>
  );
}

function RegionPicker({ cloud, onOutcome }: { cloud: Cloud; onOutcome: (outcomes: Outcome[]) => void }) {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (region: string) => put<CloudRegionSaved>(`/api/clouds/${cloud.id}/region`, { region }),
    onSuccess: (saved) => {
      const checked: Outcome[] = saved.check ? [{ tone: saved.check.ok ? "ok" : "fail", text: saved.check.ok ? `Region ${saved.region} saved. ${saved.check.message}` : `Region saved, but the check failed: ${saved.check.message}` }] : [{ tone: "note", text: `Region ${saved.region} saved.` }];
      onOutcome([...checked, ...(saved.note ? [{ tone: "note" as const, text: saved.note }] : [])]);
      void client.invalidateQueries({ queryKey: ["clouds"] });
    },
    onError: (error) => onOutcome([{ tone: "fail", text: (error as Error).message }]),
  });
  const fromEnv = cloud.region_source === "env";
  return (
    <div className="cloud-region">
      <Field id={`cloud-${cloud.id}-region`} label="Region" help={fromEnv ? <>Set by the environment variable <code>SAUCE_REGION</code>, which wins over a region chosen here.</> : "The data center of your account; the keys are refused in any other."}>
        <select className="mono" value={cloud.region ?? ""} onChange={(event) => save.mutate(event.target.value)} disabled={fromEnv || save.isPending}>
          {cloud.regions.map((region) => (
            <option key={region} value={region}>
              {region}
            </option>
          ))}
        </select>
      </Field>
    </div>
  );
}

/** One test cloud on the Integrations screen: status, what it can run here, connect, check, disconnect. */
export function CloudCard({ cloud }: { cloud: Cloud }) {
  const client = useQueryClient();
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [confirming, setConfirming] = useState(false);
  const check = useMutation({
    mutationFn: () => post<CloudCheck>(`/api/clouds/${cloud.id}/check`),
    onSuccess: (result) => setOutcomes([{ tone: result.ok ? "ok" : "fail", text: result.message }]),
    onError: (error) => setOutcomes([{ tone: "fail", text: (error as Error).message }]),
  });
  const disconnect = useMutation({
    mutationFn: () => remove<CloudRemoved>(`/api/clouds/${cloud.id}`),
    onSuccess: (result) => {
      setConfirming(false);
      setOutcomes([{ tone: "note", text: result.removed ? "Saved keys removed." : "There were no saved keys to remove." }, ...(result.note ? [{ tone: "note" as const, text: result.note }] : [])]);
      void client.invalidateQueries({ queryKey: ["clouds"] });
    },
    onError: (error) => {
      setConfirming(false);
      setOutcomes([{ tone: "fail", text: (error as Error).message }]);
    },
  });
  const fromEnv = cloud.source === "env";
  const titleId = `cloud-${cloud.id}-title`;

  return (
    <article className={`panel cloud-card${cloud.connected ? " cloud-card-on" : ""}`} aria-labelledby={titleId}>
      <header className="cloud-card-head">
        {/* The logo repeats the name's link for the mouse; keyboard and screen reader users get the name's. */}
        <a className="cloud-site" href={cloud.website_url} target="_blank" rel="noopener noreferrer" tabIndex={-1} aria-hidden="true" title={`${cloud.name} website`}>
          <CloudMark cloud={cloud.id} name={cloud.name} />
        </a>
        <div className="cloud-card-title">
          <h2 id={titleId}>
            <a className="cloud-site-name" href={cloud.website_url} target="_blank" rel="noopener noreferrer" title={`${cloud.name} website (new tab)`}>
              {cloud.name}
            </a>
          </h2>
          <span className={`cloud-status${cloud.connected ? " cloud-status-on" : ""}`}>
            <span className="cloud-status-dot" aria-hidden="true" />
            {connectionLabel(cloud)}
          </span>
        </div>
        <a className="button button-small button-ghost cloud-dashboard" href={cloud.dashboard_url} target="_blank" rel="noopener noreferrer" aria-label={`Open the ${cloud.name} dashboard (new tab)`}>
          Dashboard <Icon name="link" size={12} />
        </a>
      </header>

      <dl className="cloud-ready">
        <Readiness label="Web" reason={cloud.web_ready} />
        <Readiness label="Native apps" reason={cloud.mobile_ready} />
      </dl>
      {cloud.regions.length ? <RegionPicker cloud={cloud} onOutcome={setOutcomes} /> : cloud.region ? <p className="small muted">Region {cloud.region}</p> : null}
      {cloud.problem ? (
        <p className="callout callout-warn">
          <Icon name="alert" size={13} /> {cloud.problem}
        </p>
      ) : null}

      {fromEnv ? (
        <div className="callout callout-note cloud-env">
          <span>
            The keys come from the environment variables <code>{cloud.username_env}</code> and <code>{cloud.access_key_env}</code>, which win over keys saved here. To change or disconnect, edit or unset them where the daemon starts, then restart it.
          </span>
        </div>
      ) : cloud.connected ? (
        <details className="cloud-replace">
          <summary>Replace the saved keys</summary>
          <ConnectForm cloud={cloud} onOutcome={setOutcomes} />
        </details>
      ) : (
        <ConnectForm cloud={cloud} onOutcome={setOutcomes} />
      )}

      <div className="cloud-outcomes" role="status" aria-live="polite">
        {outcomes.map((outcome) => (
          <p key={outcome.text} className={outcome.tone === "ok" ? "notice notice-ok" : outcome.tone === "fail" ? "callout callout-fail" : "callout callout-note"}>
            <Icon name={outcome.tone === "ok" ? "check" : outcome.tone === "fail" ? "cross" : "alert"} size={13} /> {outcome.text}
          </p>
        ))}
      </div>

      {cloud.connected ? (
        <footer className="cloud-card-actions">
          <button type="button" className="button" onClick={() => check.mutate()} disabled={check.isPending}>
            <Icon name="rerun" size={13} /> {check.isPending ? "Testing…" : "Test connection"}
          </button>
          {cloud.source === "file" ? (
            confirming ? (
              <span className="cloud-confirm" role="group" aria-label="Confirm disconnect">
                <span className="small">Remove the saved keys?</span>
                <button type="button" className="button button-danger button-small" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
                  Remove
                </button>
                <button type="button" className="button button-ghost button-small" onClick={() => setConfirming(false)}>
                  Keep
                </button>
              </span>
            ) : (
              <button type="button" className="button button-ghost" onClick={() => setConfirming(true)}>
                Disconnect
              </button>
            )
          ) : null}
        </footer>
      ) : null}
    </article>
  );
}
