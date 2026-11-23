import { evidenceUrl } from "../lib/api";
import { formatPercent } from "../lib/format";
import { classificationLabel, formatRatio, pageLabel } from "../lib/quality";
import type { QualityPage, QualityVisual } from "../lib/types";
import { Icon } from "./Icon";

const IMAGE_ORDER = ["before", "after", "diff"];
const IMAGE_CAPTIONS: Record<string, string> = { before: "Baseline", after: "This run", diff: "Difference" };
const PREVIEW_SIDE = 900;

function imageAlt(name: string, page: QualityPage, baseline: string): string {
  const where = pageLabel(page).toLowerCase();
  if (name === "before") return `Baseline ${baseline} screenshot (${where})`;
  if (name === "after") return `Screenshot from this run (${where})`;
  if (name === "diff") return `Changed pixels highlighted against the baseline (${where})`;
  return `${name} image (${where})`;
}

function orderedImages(images: Record<string, string>): Array<[string, string]> {
  return Object.entries(images).sort(([a], [b]) => {
    const rank = (name: string) => (IMAGE_ORDER.includes(name) ? IMAGE_ORDER.indexOf(name) : IMAGE_ORDER.length);
    return rank(a) - rank(b) || a.localeCompare(b);
  });
}

function VisualFacts({ visual }: { visual: QualityVisual }) {
  const classification = visual.classification;
  return (
    <dl className="visual-facts">
      <div>
        <dt>Changed pixels</dt>
        <dd>
          {formatRatio(visual.diff_ratio)} <span className="muted small">threshold {formatRatio(visual.threshold)}</span>
        </dd>
      </div>
      <div>
        <dt>Result</dt>
        <dd>
          {visual.created ? (
            <span className="chip chip-pending">New baseline</span>
          ) : visual.changed ? (
            <span className="chip chip-failed">
              <Icon name="alert" size={12} /> Changed
            </span>
          ) : (
            <span className="chip chip-passed">
              <Icon name="check" size={12} /> Within threshold
            </span>
          )}
        </dd>
      </div>
      <div>
        <dt>Changed regions</dt>
        <dd>{visual.regions.length}</dd>
      </div>
      {classification ? (
        <div>
          <dt>Classified as</dt>
          <dd>
            {classificationLabel(classification.label)} <span className="muted small">{formatPercent(classification.confidence)} · {classification.engine}</span>
          </dd>
        </div>
      ) : visual.note ? (
        <div>
          <dt>Not classified</dt>
          <dd>{visual.note}</dd>
        </div>
      ) : null}
    </dl>
  );
}

/** A page's visual comparison against its baseline: verdict, style changes, missing elements, images. */
export function VisualCheck({ runId, page, headingId }: { runId: string; page: QualityPage; headingId: string }) {
  const visual = page.visual;
  if (!visual) return null;
  const images = orderedImages(visual.images);
  return (
    <article className="visual-check" aria-labelledby={headingId}>
      <h4 id={headingId}>
        {pageLabel(page)} · baseline <span className="mono">{visual.baseline}</span>
      </h4>
      <p className="muted small quality-url">
        {page.title ? `${page.title} · ` : ""}
        <span className="mono">{page.url}</span>
      </p>
      {visual.created ? (
        <p className="callout callout-note" role="note">
          <Icon name="image" size={14} /> Baseline created: this run's screenshot is now the baseline <strong className="mono">{visual.baseline}</strong>. Later runs are compared against it.
        </p>
      ) : null}
      <VisualFacts visual={visual} />
      {visual.style_deltas.length ? (
        <div className="table-wrap">
          <table className="table quality-table">
            <caption className="visually-hidden">Style changes against the baseline</caption>
            <thead>
              <tr>
                <th scope="col">Element</th>
                <th scope="col">Property</th>
                <th scope="col">Before</th>
                <th scope="col">After</th>
              </tr>
            </thead>
            <tbody>
              {visual.style_deltas.map((delta) => (
                <tr key={`${delta.key}-${delta.property}`}>
                  <td className="mono">{delta.selector}</td>
                  <td className="mono">{delta.property}</td>
                  <td className="mono">{delta.before}</td>
                  <td className="mono">{delta.after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {visual.missing.length ? (
        <div className="callout callout-warn">
          <span>
            <Icon name="alert" size={14} /> Key elements missing from the page:
          </span>
          <ul className="quality-missing">
            {visual.missing.map((name) => (
              <li key={name} className="mono">
                {name}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {images.length ? (
        <div className="visual-images">
          {images.map(([name, ref]) => (
            <figure key={name} className="visual-image">
              <a href={evidenceUrl(runId, ref)} target="_blank" rel="noopener" title="Open full size">
                <img src={`${evidenceUrl(runId, ref)}?max_side=${PREVIEW_SIDE}`} alt={imageAlt(name, page, visual.baseline)} loading="lazy" decoding="async" />
              </a>
              <figcaption>{IMAGE_CAPTIONS[name] ?? name}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}
    </article>
  );
}
