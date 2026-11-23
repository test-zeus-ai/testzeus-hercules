import { evidenceName, evidenceUrl } from "../lib/api";
import { failingFindings, groupBelowGate, SEVERITY_CLASS, SEVERITY_LABELS } from "../lib/security";
import { SEVERITIES, type SecurityFinding, type SecurityScan, type SecuritySection } from "../lib/types";
import { Icon } from "./Icon";

const VISIBLE_PLACES = 3;

function FailingFinding({ finding }: { finding: SecurityFinding }) {
  return (
    <li>
      <article className={`finding ${SEVERITY_CLASS[finding.severity]}`} aria-label={`${SEVERITY_LABELS[finding.severity]}: ${finding.template_id}`}>
        <div className="finding-head">
          <span className={`chip impact ${SEVERITY_CLASS[finding.severity]}`}>{SEVERITY_LABELS[finding.severity]}</span>
          <strong className="mono">
            {finding.template_id}
            {finding.matcher ? `:${finding.matcher}` : ""}
          </strong>
          {finding.name ? <span className="muted small">{finding.name}</span> : null}
        </div>
        <p className="small mono security-where">{finding.matched_at}</p>
        {finding.extracted.length ? <p className="small">Extracted: {finding.extracted.join(", ")}</p> : null}
        {finding.description ? <p className="muted small">{finding.description}</p> : null}
      </article>
    </li>
  );
}

function ScanEntry({ runId, scan }: { runId: string; scan: SecurityScan }) {
  const failing = failingFindings(scan);
  const others = groupBelowGate(scan);
  return (
    <li className="quality-page security-scan">
      <div className="quality-page-head">
        <strong>{scan.step !== null ? `Step ${scan.step}` : "Scan"}</strong>
        <span className="tag">{scan.input === "openapi" ? "OpenAPI" : "URL"}</span>
        <span className="muted small">
          {scan.counts.findings} finding{scan.counts.findings === 1 ? "" : "s"} · fails on {scan.fail_on} or above · {(scan.duration_ms / 1000).toFixed(1)} s
        </span>
      </div>
      <p className="muted small quality-url">
        <span className="mono">{scan.target}</span> · tags {scan.tags.join(", ")}
        {scan.exclude_tags.length ? ` · excluded ${scan.exclude_tags.join(", ")}` : ""}
        {scan.scanner ? ` · ${scan.scanner}` : ""}
      </p>
      {scan.error ? (
        <p className="callout callout-fail">
          <Icon name="alert" size={14} /> The scan did not finish: {scan.error}
        </p>
      ) : null}
      {failing.length ? (
        <>
          <p className="callout callout-fail">
            <Icon name="cross" size={14} /> {failing.length} finding{failing.length === 1 ? "" : "s"} at {scan.fail_on} or above fail{failing.length === 1 ? "s" : ""} this step.
          </p>
          <ul className="finding-list">
            {failing.map((finding) => (
              <FailingFinding key={`${finding.template_id}|${finding.matcher ?? ""}|${finding.matched_at}`} finding={finding} />
            ))}
          </ul>
        </>
      ) : scan.error ? null : (
        <p className="callout callout-note">
          <Icon name="check" size={14} /> No finding at {scan.fail_on} or above.
        </p>
      )}
      {others.length ? (
        <details className="quality-more">
          <summary>
            {others.length} other template{others.length === 1 ? "" : "s"} matched below {scan.fail_on}
          </summary>
          <ul className="security-groups">
            {others.map((group) => (
              <li key={group.key}>
                <span className={`chip impact ${SEVERITY_CLASS[group.severity]}`}>{SEVERITY_LABELS[group.severity]}</span> <span className="mono">{group.templateId}</span>{" "}
                <span className="muted small">
                  {group.name}
                  {group.matchers.length ? ` (${group.matchers.join(", ")})` : ""} · {group.places.slice(0, VISIBLE_PLACES).join(", ")}
                  {group.places.length > VISIBLE_PLACES ? ` and ${group.places.length - VISIBLE_PLACES} more` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {scan.evidence.length ? (
        <ul className="evidence-links">
          {scan.evidence.map((ref) => (
            <li key={ref}>
              <a href={evidenceUrl(runId, ref)} target="_blank" rel="noopener">
                <Icon name="link" size={13} /> {evidenceName(ref)}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** The run's security scans: findings per severity over all scans, then each scan with what fails it. */
export function SecurityPanel({ runId, security }: { runId: string; security: SecuritySection }) {
  const { counts } = security;
  return (
    <section className="panel panel-quality panel-security" aria-labelledby="security-title">
      <div className="panel-head">
        <h2 id="security-title">
          <Icon name="shield" size={16} /> Security scans
        </h2>
        <span className="muted small">
          {security.scans.length} scan{security.scans.length === 1 ? "" : "s"}
        </span>
      </div>
      <dl className="quality-counts" aria-label="Findings by severity">
        {SEVERITIES.map((severity) => (
          <div key={severity} className={`quality-count ${SEVERITY_CLASS[severity]}${counts[severity] ? "" : " quality-count-zero"}`}>
            <dt>{SEVERITY_LABELS[severity]}</dt>
            <dd>{counts[severity]}</dd>
          </div>
        ))}
      </dl>
      <ul className="quality-pages">
        {security.scans.map((scan, index) => (
          <ScanEntry key={`${scan.step ?? "scan"}-${index}`} runId={runId} scan={scan} />
        ))}
      </ul>
    </section>
  );
}
