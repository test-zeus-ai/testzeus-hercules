import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import { CloudCard } from "../components/CloudCard";
import { Icon } from "../components/Icon";
import { api } from "../lib/api";
import { shortPath } from "../lib/format";
import { isCloudsReport } from "../lib/guards";
import type { CloudsReport } from "../lib/types";

export function IntegrationsScreen() {
  const report = useQuery({ queryKey: ["clouds"], queryFn: () => api<CloudsReport>("/api/clouds", { guard: isCloudsReport }) });
  const data = report.data;
  return (
    <section className="page" aria-labelledby="integrations-title">
      <header className="page-head">
        <div>
          <h1 id="integrations-title">Integrations</h1>
          <p className="page-sub">Connect a test cloud, then run any passed test on it from the run's page, or download it as a project for that cloud's CI.</p>
        </div>
      </header>
      {report.isError ? <p className="callout callout-fail">Could not read the clouds: {(report.error as Error).message}</p> : null}
      {!data && !report.isError ? <p className="muted">Loading…</p> : null}
      {data && !data.egress_allows ? (
        <p className="callout callout-warn" role="note">
          <Icon name="alert" size={14} /> The egress policy is <strong>local</strong>, so nothing may leave this machine and cloud runs are blocked. You can still connect clouds and download projects for CI.{" "}
          <Link href="/models">How to change the egress policy</Link>
        </p>
      ) : null}
      {data ? (
        <>
          <div className="cards cloud-cards">
            {data.clouds.map((cloud) => (
              <CloudCard key={cloud.id} cloud={cloud} />
            ))}
          </div>
          <p className="muted small cloud-footnote">
            <Icon name="key" size={12} /> Keys are saved to <span className="mono" title={data.keys_file}>{shortPath(data.keys_file)}</span>, readable only by you; environment variables win over them. Access keys are never shown again or sent back to this page.
          </p>
        </>
      ) : null}
    </section>
  );
}
