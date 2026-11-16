import { useMemo, type ReactNode } from "react";
import { evidenceName, evidenceUrl } from "../lib/api";
import { annotatedScreenshot, failingCount, groupFindings, IMPACT_LABELS, KIND_LABELS, pageLabel, thresholdLabel } from "../lib/quality";
import { IMPACTS, type QualityPage, type QualitySection } from "../lib/types";
import { Icon } from "./Icon";
import { FindingCard } from "./QualityFinding";
import { VisualCheck } from "./VisualCheck";

const VISIBLE_FINDINGS = 5;
const VISIBLE_PAGES = 4;
const SHOT_SIDE = 720;

/** The first `limit` items, the rest behind a keyboard-usable disclosure. */
function Collapsible({ items, limit, more, className }: { items: ReactNode[]; limit: number; more: string; className: string }) {
  const shown = items.slice(0, limit);
  const rest = items.slice(limit);
  return (
    <>
      <ul className={className}>{shown}</ul>
      {rest.length ? (
        <details className="quality-more">
          <summary>
            Show {rest.length} more {more}
          </summary>
          <ul className={className}>{rest}</ul>
        </details>
      ) : null}
    </>
  );
}

function Summary({ quality }: { quality: QualitySection }) {
  const { counts, fail_on: failOn } = quality;
  const failing = failOn ? failingCount(counts, failOn) : 0;
  return (
    <>
      <dl className="quality-counts">
        {IMPACTS.map((impact) => (
          <div key={impact} className={`quality-count impact-${impact}${counts[impact] ? "" : " quality-count-zero"}`}>
            <dt>{IMPACT_LABELS[impact]}</dt>
            <dd>{counts[impact]}</dd>
          </div>
        ))}
        <div className="quality-count">
          <dt>Fixes</dt>
          <dd>
            {counts.fixes} <span className="muted small">{counts.verified_fixes} verified</span>
          </dd>
        </div>
        <div className="quality-count">
          <dt>Pages</dt>
          <dd>{counts.pages}</dd>
        </div>
      </dl>
      {failOn ? (
        failing > 0 ? (
          <p className="callout callout-fail">
            <Icon name="cross" size={14} /> {failing} finding{failing === 1 ? "" : "s"} at {thresholdLabel(failOn)} fail{failing === 1 ? "s" : ""} this run.
          </p>
        ) : (
          <p className="callout callout-note">
            <Icon name="check" size={14} /> The run fails on {thresholdLabel(failOn)} findings; there are none.
          </p>
        )
      ) : (
        <p className="muted small">Report only: findings do not fail the run.</p>
      )}
    </>
  );
}

function PageEntry({ runId, page }: { runId: string; page: QualityPage }) {
  const shot = annotatedScreenshot(page);
  const files = page.evidence.filter((ref) => ref !== shot);
  const where = pageLabel(page);
  return (
    <li className="quality-page">
      <div className="quality-page-head">
        <strong>{where}</strong>
        <span className="tag">{KIND_LABELS[page.kind] ?? page.kind}</span>
        <span className="muted small">
          {page.findings.length} finding{page.findings.length === 1 ? "" : "s"}
          {page.fixes.length ? `, ${page.fixes.length} fix${page.fixes.length === 1 ? "" : "es"}` : ""}
        </span>
      </div>
      <p className="muted small quality-url">
        {page.title ? `${page.title} · ` : ""}
        <span className="mono">{page.url}</span>
      </p>
      {page.error ? (
        <p className="callout callout-fail">
          <Icon name="alert" size={14} /> The check did not complete: {page.error}
        </p>
      ) : null}
      {page.not_scanned.length ? (
        <div className="callout callout-note" role="note">
          <span>Not scanned (the audit could not reach these frames):</span>
          <ul className="quality-missing">
            {page.not_scanned.map((frame) => (
              <li key={frame} className="mono">
                {frame}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {shot ? (
        <figure className="quality-shot">
          <a href={evidenceUrl(runId, shot)} target="_blank" rel="noopener" title="Open full size">
            <img src={`${evidenceUrl(runId, shot)}?max_side=${SHOT_SIDE}`} alt={`Annotated screenshot, ${where.toLowerCase()}: numbered boxes mark the findings in order`} loading="lazy" decoding="async" />
          </a>
          <figcaption className="muted small">Numbered boxes follow the findings on this page.</figcaption>
        </figure>
      ) : null}
      {files.length ? (
        <ul className="evidence-links">
          {files.map((ref) => (
            <li key={ref}>
              <a href={evidenceUrl(runId, ref)} target="_blank" rel="noopener">
                <Icon name={/\.(jpe?g|png)$/i.test(ref) ? "image" : "link"} size={13} /> {evidenceName(ref)}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** Accessibility, visual and layout results of a run: findings by impact with their fixes, then each page checked. */
export function QualityPanel({ runId, quality }: { runId: string; quality: QualitySection }) {
  const groups = useMemo(() => groupFindings(quality.pages), [quality.pages]);
  const visualPages = quality.pages.map((page, index) => ({ page, index })).filter(({ page }) => page.visual);
  return (
    <section className="panel panel-quality" aria-labelledby="quality-title">
      <div className="panel-head">
        <h2 id="quality-title">
          <Icon name="eye" size={16} /> Quality checks
        </h2>
        <span className="run-chips">
          {quality.a11y ? <span className="tag">Accessibility</span> : null}
          {quality.visual ? <span className="tag">Visual</span> : null}
        </span>
      </div>
      <Summary quality={quality} />

      <section className="quality-section" aria-labelledby="quality-findings-title">
        <h3 id="quality-findings-title">Findings</h3>
        {groups.length === 0 ? (
          <p className="empty-note">No findings on the {quality.pages.length} page{quality.pages.length === 1 ? "" : "s"} checked.</p>
        ) : (
          groups.map((group) => (
            <section key={group.impact} className="quality-group" aria-labelledby={`quality-group-${group.impact}`}>
              <h4 id={`quality-group-${group.impact}`}>
                {IMPACT_LABELS[group.impact]} <span className="muted">· {group.findings.length}</span>
              </h4>
              <Collapsible
                className="finding-list"
                limit={VISIBLE_FINDINGS}
                more={`${group.impact} findings`}
                items={group.findings.map((entry) => (
                  <li key={entry.finding.id}>
                    <FindingCard entry={entry} />
                  </li>
                ))}
              />
            </section>
          ))
        )}
      </section>

      {visualPages.length ? (
        <section className="quality-section" aria-labelledby="quality-visual-title">
          <h3 id="quality-visual-title">Visual comparisons</h3>
          {visualPages.map(({ page, index }) => (
            <VisualCheck key={index} runId={runId} page={page} headingId={`quality-visual-${index}`} />
          ))}
        </section>
      ) : null}

      <section className="quality-section" aria-labelledby="quality-pages-title">
        <h3 id="quality-pages-title">Pages checked</h3>
        <Collapsible className="quality-pages" limit={VISIBLE_PAGES} more="pages" items={quality.pages.map((page, index) => <PageEntry key={index} runId={runId} page={page} />)} />
      </section>
    </section>
  );
}
