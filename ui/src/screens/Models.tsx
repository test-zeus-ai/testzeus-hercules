import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Icon } from "../components/Icon";
import { api } from "../lib/api";
import { isModelsReport } from "../lib/guards";
import type { ModelsReport } from "../lib/types";

function YesNo({ value, yes = "yes", no = "no" }: { value: boolean | null | undefined; yes?: string; no?: string }) {
  if (value === null || value === undefined) return <span className="muted">unknown</span>;
  return (
    <span className={value ? "text-pass" : "text-fail"}>
      <Icon name={value ? "check" : "cross"} size={12} /> {value ? yes : no}
    </span>
  );
}

const EGRESS_TEXT: Record<string, string> = {
  local: "Nothing leaves this machine. Only the local model decides (pure mode).",
  "hybrid-text": "Page text (with password and secret fields masked) may go to JEV for decisions. Screenshots stay local.",
  hybrid: "Page text may go to JEV, and screenshots too when JEV is unsure. Cookies and storage are never sent.",
};

export function ModelsScreen() {
  const [probe, setProbe] = useState(false);
  const report = useQuery({ queryKey: ["models", probe], queryFn: () => api<ModelsReport>(`/api/models${probe ? "?probe=true" : ""}`, { guard: isModelsReport }), staleTime: probe ? 60_000 : 5_000 });
  const data = report.data;
  const doctor = data?.doctor;

  return (
    <section className="page" aria-labelledby="models-title">
      <header className="page-head">
        <div>
          <h1 id="models-title">Models</h1>
          <p className="page-sub">The local model server, what it can do, and which run modes are available.</p>
        </div>
        <button type="button" className="button" onClick={() => (probe ? void report.refetch() : setProbe(true))} disabled={report.isFetching}>
          <Icon name="rerun" size={14} /> {report.isFetching && probe ? "Checking…" : "Run doctor"}
        </button>
      </header>
      {report.isError ? <p className="callout callout-fail">Could not read the model report: {(report.error as Error).message}</p> : null}
      {!data ? <p className="muted">Loading…</p> : null}
      {data ? (
        <div className="cards">
          <section className="panel card" aria-labelledby="server-title">
            <h2 id="server-title">Local model</h2>
            <dl className="kv">
              <dt>Provider</dt>
              <dd className="mono">{data.provider}</dd>
              <dt>Model</dt>
              <dd className="mono">{data.model}</dd>
              <dt>Endpoint</dt>
              <dd className="mono">{data.endpoint ? data.endpoint.base_url : "not configured"}</dd>
              <dt>On this machine</dt>
              <dd>
                <YesNo value={data.endpoint?.is_local} />
              </dd>
              <dt>Hardware tier</dt>
              <dd>
                {doctor?.tier?.name ?? data.tier}
                {doctor?.tier ? <span className="muted"> · {doctor.tier.context_tokens.toLocaleString()} tokens context</span> : null}
              </dd>
              <dt>Parallel runs</dt>
              <dd>{data.parallel_runs}</dd>
            </dl>
          </section>

          <section className="panel card" aria-labelledby="modes-title">
            <h2 id="modes-title">Run modes</h2>
            <div className="mode">
              <h3>
                Pure <YesNo value={data.modes.pure.available} yes="available" no="unavailable" />
              </h3>
              <p className="muted">The local model reads steps and makes every decision.</p>
              {data.modes.pure.reason ? <p className="small">{data.modes.pure.reason}</p> : null}
            </div>
            <div className="mode">
              <h3>
                Hybrid <YesNo value={data.modes.hybrid.available} yes="available" no="unavailable" />
              </h3>
              <p className="muted">JEV makes the decisions; the local model does the rest and covers JEV outages.</p>
              <dl className="kv">
                <dt>JEV key</dt>
                <dd>
                  <code>{data.modes.hybrid.key_env}</code> <YesNo value={data.modes.hybrid.key_set} yes="set" no="not set" />
                </dd>
              </dl>
              <p className="small muted">The key's value is never stored, returned or shown; only whether the variable is set in the daemon's environment.</p>
              {data.modes.hybrid.reason ? <p className="small">{data.modes.hybrid.reason}</p> : null}
            </div>
          </section>

          <section className="panel card" aria-labelledby="egress-title">
            <h2 id="egress-title">Egress policy</h2>
            <p className="egress-value">
              <span className={`tag tag-egress-${data.egress_policy}`}>{data.egress_policy}</span>
            </p>
            <p>{EGRESS_TEXT[data.egress_policy] ?? ""}</p>
            <h3>How to change it</h3>
            <ul className="howto">
              <li>
                For the project, in <code>.hercules/config.yaml</code>:
                <pre className="code-block">{`egress:\n  policy: hybrid-text`}</pre>
              </li>
              <li>
                For the daemon: start it with <code>HERCULES_EGRESS=hybrid-text hercules serve</code>.
              </li>
              <li>
                For one run: pick an egress in <em>Start a run</em> (or <code>hercules run --egress …</code>).
              </li>
            </ul>
            <p className="small muted">The policy is read when the daemon starts; restart it after editing the file.</p>
          </section>

          <section className="panel card card-wide" aria-labelledby="caps-title">
            <h2 id="caps-title">Capabilities</h2>
            {data.capabilities.length === 0 && !doctor?.capabilities ? (
              <p className="empty-note">No capability probe cached yet. Run the doctor (here or <code>hercules doctor</code>) to probe the server.</p>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">Server</th>
                      <th scope="col">Model</th>
                      <th scope="col" className="num">
                        Context
                      </th>
                      <th scope="col">Tools</th>
                      <th scope="col">JSON schema</th>
                      <th scope="col">Images</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.capabilities.map((cap) => (
                      <tr key={`${cap.base_url}-${cap.model_id}`}>
                        <td className="mono">
                          {cap.base_url}
                          {cap.server_version ? <span className="muted"> · {cap.server_version}</span> : null}
                        </td>
                        <td className="mono">{cap.model_id ?? "–"}</td>
                        <td className="num">{cap.context_tokens ? cap.context_tokens.toLocaleString() : "–"}</td>
                        <td>
                          <YesNo value={cap.supports_tools} />
                        </td>
                        <td>
                          <YesNo value={cap.supports_json_schema} />
                        </td>
                        <td>{cap.image_mode ?? "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {doctor ? (
            <section className="panel card card-wide" aria-labelledby="doctor-title">
              <h2 id="doctor-title">
                Doctor <span className={`tag tag-check-${doctor.status}`}>{doctor.status}</span>
              </h2>
              {doctor.detail ? <p>{doctor.detail}</p> : null}
              <ul className="checks">
                {(doctor.checks ?? []).map((check) => (
                  <li key={check.name} className={`check check-${check.status}`}>
                    <span className={`tag tag-check-${check.status}`}>{check.status}</span>
                    <span className="check-name">{check.name}</span>
                    <span className="check-detail">{check.detail}</span>
                    {check.fix ? <span className="check-fix">Fix: {check.fix}</span> : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="muted small card-wide">The doctor probes the model server (a short request). It is not run automatically.</p>
          )}
        </div>
      ) : null}
    </section>
  );
}
