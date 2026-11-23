import { formatPercent } from "../lib/format";
import { formatValue, humanKey, IMPACT_LABELS, KIND_LABELS, methodLabel, pageLabel, safeHref, type GroupedFinding } from "../lib/quality";
import type { FixSuggestion, Impact } from "../lib/types";
import { Icon } from "./Icon";
import { Snippet } from "./Snippet";

export function ImpactChip({ impact }: { impact: Impact }) {
  return <span className={`chip impact impact-${impact}`}>{IMPACT_LABELS[impact] ?? impact}</span>;
}

function Measurements({ label, values }: { label: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (!entries.length) return null;
  return (
    <div className="measured">
      <span className="measured-label">{label}</span>
      <dl className="measured-values">
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{humanKey(key)}</dt>
            <dd className="mono">{formatValue(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function VerifiedBadge({ verified }: { verified: boolean }) {
  return verified ? (
    <span className="chip chip-passed" title="Applied in the live page; the re-check passed">
      <Icon name="check" size={12} /> Verified
    </span>
  ) : (
    <span className="chip chip-fix-unverified" title="Not applied in the page; review it before using it">
      <Icon name="alert" size={12} /> Not verified
    </span>
  );
}

function FixCard({ fix, findingSelector, rule }: { fix: FixSuggestion; findingSelector: string; rule: string }) {
  const extra = Object.entries(fix.changes).filter(([key, value]) => !(key === fix.property_or_attr && value === fix.suggested));
  return (
    <div className={`fix${fix.verified ? " fix-verified" : " fix-unverified"}`}>
      <div className="fix-head">
        <span className="fix-title">Suggested fix</span>
        <VerifiedBadge verified={fix.verified} />
        <span className="muted small">
          {methodLabel(fix.method)} · {formatPercent(fix.confidence)} confidence
        </span>
      </div>
      <p className="fix-change">
        <code>{fix.property_or_attr}</code>
        {fix.state ? <span className="muted"> ({fix.state})</span> : null}:{" "}
        {fix.current !== null ? (
          <>
            <code className="fix-current">{fix.current}</code>
            <span aria-hidden="true"> → </span>
            {" "}
            <span className="visually-hidden">to</span>{" "}
          </>
        ) : null}
        <code className="fix-suggested">{fix.suggested}</code>
      </p>
      {extra.length ? (
        <ul className="fix-changes">
          {extra.map(([key, value]) => (
            <li key={key}>
              <code>{key}</code>: <code>{value}</code>
            </li>
          ))}
        </ul>
      ) : null}
      {fix.target_selector && fix.target_selector !== findingSelector ? (
        <p className="small">
          Apply to <code>{fix.target_selector}</code>
        </p>
      ) : null}
      {fix.after_fix ? <Measurements label="After the fix" values={fix.after_fix} /> : null}
      {fix.likely_file ? (
        <p className="small">
          Likely in <code>{fix.likely_file}</code>
        </p>
      ) : null}
      {fix.rationale ? <p className="fix-rationale small">{fix.rationale}</p> : null}
      {!fix.verified ? <p className="small fix-warning">Not checked in the page: review this suggestion before applying it.</p> : null}
      {fix.snippet ? <Snippet text={fix.snippet} label={`fix for ${rule}`} /> : null}
    </div>
  );
}

/** One finding (seen on one or more pages) with what was measured and the fixes suggested. */
export function FindingCard({ entry }: { entry: GroupedFinding }) {
  const { finding, occurrences, fixes } = entry;
  const help = safeHref(finding.help_url);
  return (
    <article className={`finding finding-${finding.impact}`} aria-label={`${IMPACT_LABELS[finding.impact]}: ${finding.rule} on ${finding.element_desc || finding.selector}`}>
      <header className="finding-head">
        <ImpactChip impact={finding.impact} />
        <code className="finding-rule">{finding.rule}</code>
        {finding.wcag ? (
          <span className="tag">
            WCAG {finding.wcag}
            {finding.level !== "none" ? ` · ${finding.level}` : ""}
          </span>
        ) : finding.level !== "none" ? (
          <span className="tag">Level {finding.level}</span>
        ) : null}
        <span className="tag">{KIND_LABELS[finding.kind] ?? finding.kind}</span>
      </header>
      <p className="finding-message">{finding.message}</p>
      <p className="finding-element small">
        {finding.element_desc ? <span>{finding.element_desc} </span> : null}
        <code>{finding.selector}</code>
        {finding.frame_path.length ? (
          <span className="muted">
            {" "}
            in frame <code>{finding.frame ?? finding.frame_path.join(" › ")}</code>
          </span>
        ) : null}
      </p>
      {finding.measured ? <Measurements label="Measured" values={finding.measured} /> : null}
      {finding.hints.length ? (
        <ul className="finding-hints small">
          {finding.hints.map((hint) => (
            <li key={hint}>{hint}</li>
          ))}
        </ul>
      ) : null}
      <p className="finding-where small muted">
        Found on{" "}
        {occurrences.map((occurrence, offset) => (
          <span key={`${occurrence.pageIndex}-${offset}`}>
            {offset > 0 ? ", " : ""}
            <span title={occurrence.page.url}>{pageLabel(occurrence.page).toLowerCase()}</span>
            {occurrence.box !== null ? ` (box ${occurrence.box})` : ""}
          </span>
        ))}
        {help ? (
          <>
            {" · "}
            <a href={help} target="_blank" rel="noopener noreferrer">
              Rule reference <span className="visually-hidden">for {finding.rule} (opens in a new tab)</span>
            </a>
          </>
        ) : null}
      </p>
      {fixes.length ? (
        fixes.map((fix, offset) => <FixCard key={offset} fix={fix} findingSelector={finding.selector} rule={finding.rule} />)
      ) : (
        <p className="small muted">No fix suggested.</p>
      )}
    </article>
  );
}
