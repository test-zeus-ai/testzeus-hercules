import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "wouter";
import { api, post } from "../lib/api";
import {
  EMPTY_CLOUD_RUN_FORM,
  browserLabel,
  cloudBundleUrl,
  cloudRunBlocker,
  cloudRunBody,
  connectionLabel,
  defaultCloud,
  osLabel,
  validateCloudRunForm,
  type CloudKind,
  type CloudRunForm,
} from "../lib/clouds";
import { isCloudRun, isCloudsReport } from "../lib/guards";
import type { CloudId, CloudRun, CloudsReport } from "../lib/types";
import { CloudMark } from "./CloudCard";
import { Field } from "./Field";
import { Icon } from "./Icon";
import { Modal } from "./Modal";

/**
 * "Run on a cloud" for a passed run: pick a connected cloud and a browser and OS (web) or a
 * device (native app), start the exported test there, or download it as a project for the cloud's CI.
 */
export function RunOnCloudDialog({ runId, native = false, onClose, onStarted }: { runId: string; native?: boolean; onClose: () => void; onStarted: (run: CloudRun) => void }) {
  const kind: CloudKind = native ? "mobile" : "web";
  const report = useQuery({ queryKey: ["clouds"], queryFn: () => api<CloudsReport>("/api/clouds", { guard: isCloudsReport }) });
  const data = report.data;
  const [picked, setPicked] = useState<CloudId | null>(null);
  const [form, setForm] = useState<CloudRunForm>(EMPTY_CLOUD_RUN_FORM);
  const start = useMutation({
    mutationFn: (cloud: CloudId) => post<CloudRun>(`/api/runs/${encodeURIComponent(runId)}/cloud-runs`, cloudRunBody(cloud, form, kind), isCloudRun),
    onSuccess: onStarted,
  });

  const cloudId = picked ?? (data ? defaultCloud(data.clouds, kind, data.egress_allows) : null);
  const cloud = data?.clouds.find((item) => item.id === cloudId) ?? null;
  const blocker = cloud && data ? cloudRunBlocker(cloud, kind, data.egress_allows) : null;
  const errors = validateCloudRunForm(form, kind);
  const valid = Object.keys(errors).length === 0;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (cloud && !blocker && valid) start.mutate(cloud.id);
  };

  return (
    <Modal title="Run on a cloud" onClose={onClose} wide>
      <form className="cloud-run-dialog" onSubmit={submit} noValidate>
        <p className="muted small">The test this run exported runs on the cloud's own {native ? "devices" : "browsers"}; follow it here until it passes or fails, with a link to the session.</p>
        {report.isError ? (
          <p className="callout callout-fail" role="alert">
            Could not read the clouds: {(report.error as Error).message}
          </p>
        ) : null}
        {!data && !report.isError ? <p className="muted">Loading clouds…</p> : null}
        {data && !data.egress_allows ? (
          <p className="callout callout-warn">
            <Icon name="alert" size={13} /> The egress policy is local, so nothing may leave this machine. You can still download the project for CI.
          </p>
        ) : null}
        {data ? (
          <fieldset className="cloud-picker">
            <legend className="field-label">Cloud</legend>
            {data.clouds.map((item) => {
              const reason = cloudRunBlocker(item, kind, data.egress_allows);
              const reasonId = `pick-${item.id}-reason`;
              return (
                <label key={item.id} className={`cloud-option${item.id === cloudId ? " cloud-option-on" : ""}${reason ? " cloud-option-blocked" : ""}`}>
                  <input type="radio" name="cloud" value={item.id} checked={item.id === cloudId} onChange={() => setPicked(item.id)} aria-describedby={reasonId} />
                  <CloudMark cloud={item.id} name={item.name} small />
                  <span className="cloud-option-body">
                    <span className="cloud-option-name">{item.name}</span>
                    <span id={reasonId} className={`small ${reason ? "cloud-option-reason" : "muted"}`}>
                      {reason ?? connectionLabel(item)}
                    </span>
                  </span>
                  {reason ? <span className="tag">can't run</span> : <span className="tag tag-check-ok">ready</span>}
                </label>
              );
            })}
            {data.clouds.some((item) => !item.connected) ? (
              <p className="small cloud-picker-hint">
                <Link href="/integrations">Connect a cloud under Integrations</Link>
              </p>
            ) : null}
          </fieldset>
        ) : null}

        {data ? (
          native ? (
            <div className="field-row">
              <Field id="cloud-run-device" label="Device" error={errors.device} help="Empty: the device this run used.">
                <input className="mono" value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value })} placeholder="Google Pixel 8" />
              </Field>
              <Field id="cloud-run-version" label="OS version" error={errors.platformVersion} help="Empty: the run's version.">
                <input className="mono" value={form.platformVersion} onChange={(event) => setForm({ ...form, platformVersion: event.target.value })} placeholder="14" />
              </Field>
            </div>
          ) : (
            <div className="field-row">
              <Field id="cloud-run-browser" label="Browser">
                <select value={form.browser} onChange={(event) => setForm({ ...form, browser: event.target.value })}>
                  <option value="">The run's browser</option>
                  {data.browsers.map((browser) => (
                    <option key={browser} value={browser}>
                      {browserLabel(browser)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field id="cloud-run-os" label="Operating system">
                <select value={form.os} onChange={(event) => setForm({ ...form, os: event.target.value })}>
                  {data.os_names.map((name) => (
                    <option key={name} value={name}>
                      {osLabel(name)}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          )
        ) : null}

        {start.error ? (
          <p className="callout callout-fail" role="alert">
            {(start.error as Error).message}
          </p>
        ) : null}

        {cloud ? (
          <footer className="cloud-run-actions">
            <a className="button" href={valid ? cloudBundleUrl(runId, cloud.id, form, kind) : undefined} aria-disabled={!valid || undefined} download title={`A ready-to-run project with ${cloud.ci_config}, run by ${cloud.ci_runner}`}>
              <Icon name="save" size={13} /> Download project for {cloud.name} CI (.zip)
            </a>
            <button type="submit" className="button button-primary" disabled={Boolean(blocker) || !valid || start.isPending} aria-describedby={blocker ? `pick-${cloud.id}-reason` : undefined}>
              <Icon name="cloud" size={14} /> {start.isPending ? "Starting…" : `Run on ${cloud.name}`}
            </button>
          </footer>
        ) : null}
        {cloud ? (
          <p className="muted small">
            The project includes <span className="mono">{cloud.ci_config}</span> and runs with <span className="mono">{cloud.ci_runner}</span>; set <span className="mono">{cloud.username_env}</span> and <span className="mono">{cloud.access_key_env}</span> in CI.
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
