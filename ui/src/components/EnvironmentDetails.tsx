import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { BROWSER_CHOICES } from "../lib/browserOptionsForm";
import { environmentVariables, secretVariables } from "../lib/environmentForm";
import type { Environment, ReferenceFiles, SecretSource, SecretStatus } from "../lib/types";
import { Icon } from "./Icon";

const SOURCE_LABEL: Record<SecretSource, string> = { env: "set in the daemon's environment", file: "saved in Hercules", none: "not set" };

function shown(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function EnvironmentDetails({ env, onEdit, onDelete, deleting, error }: { env: Environment; onEdit: () => void; onDelete: () => void; deleting: boolean; error: string | null }) {
  const variables = environmentVariables(env);
  const secrets = secretVariables(variables);
  const status = useQuery({
    queryKey: ["secrets", secrets.join(",")],
    queryFn: () => api<{ secrets: SecretStatus[]; file: string }>(`/api/secrets?names=${encodeURIComponent(secrets.join(","))}`),
    enabled: secrets.length > 0,
  });
  const sources = new Map((status.data?.secrets ?? []).map((item) => [item.name, item.source]));
  const files = useQuery({ queryKey: ["environment-files", env.name], queryFn: () => api<ReferenceFiles>(`/api/environments/${encodeURIComponent(env.name)}/files`) });
  const app = env.mobile;
  const rows: [string, ReactNode][] = app
    ? [
        ["Target", `Native app (${app.platform === "ios" ? "iOS" : "Android"})`],
        ["Runs on", app.cloud === "browserstack" ? "BrowserStack" : app.cloud === "saucelabs" ? "Sauce Labs" : "Local Appium server"],
        ["App", app.app],
        ["Package", app.package],
        ["Activity", app.activity],
        ["Bundle id", app.bundle_id],
        ["Device", app.device],
        ["OS version", app.platform_version],
        ["Appium server", app.cloud ? null : app.appium_url],
        ["Base URL", env.base_url],
      ]
    : [
        ["Target", "Web app (browser)"],
        ["Base URL", env.base_url],
        ["Phone or tablet", env.device],
        ["Viewport", env.viewport],
        ["Browser", BROWSER_CHOICES.find((choice) => choice.value === (env.browser ?? ""))?.label ?? env.browser],
      ];
  const options = Object.entries(env.browser_options ?? {});
  return (
    <section className="panel env-details" aria-labelledby="env-details-title">
      <div className="panel-head">
        <h2 id="env-details-title" className="mono">
          {env.name}
        </h2>
        <div className="editor-actions">
          <button type="button" className="button button-small" onClick={onEdit}>
            Edit environment
          </button>
          <button type="button" className="button button-small button-danger" onClick={onDelete} disabled={deleting}>
            Delete environment
          </button>
        </div>
      </div>
      {error ? (
        <p className="callout callout-fail" role="alert">
          {error}
        </p>
      ) : null}
      <dl className="kv">
        {rows
          .filter(([, value]) => value !== null && value !== undefined && value !== "")
          .map(([label, value]) => (
            <div key={label} className="kv-row">
              <dt>{label}</dt>
              <dd className={label === "Target" || label === "Runs on" || label === "Browser" ? undefined : "mono"}>{value}</dd>
            </div>
          ))}
        {options.length ? (
          <div className="kv-row">
            <dt>Browser options</dt>
            <dd>
              <ul className="plain-list mono">
                {options.map(([key, value]) => (
                  <li key={key}>
                    {key}: {shown(value)}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
      </dl>

      <h3>Variables and secrets</h3>
      {variables.length ? (
        <dl className="kv">
          {variables.map((row) => (
            <div key={row.key} className="kv-row">
              <dt className="mono">{`{{${row.key}}}`}</dt>
              <dd className="mono">
                {row.secret ? (
                  <span className={`tag secret-source secret-source-${sources.get(row.value) ?? "none"}`}>
                    <Icon name="key" size={11} /> {row.value}
                    {sources.get(row.value) ? `: ${SOURCE_LABEL[sources.get(row.value)!]}` : ""}
                  </span>
                ) : (
                  row.value
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="muted small">None.</p>
      )}

      <h3>Reference files</h3>
      {files.data?.files.length ? (
        <ul className="env-file-list">
          {files.data.files.map((file) => (
            <li key={file.name}>
              <span className="mono">{file.name}</span>
              <span className="muted small">{file.kind === "text" ? `${file.chars.toLocaleString()} characters read` : "image"}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">None.</p>
      )}
    </section>
  );
}
