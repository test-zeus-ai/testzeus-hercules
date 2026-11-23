// Quality sections for tests: two pages audited for accessibility and one visual comparison.

import type { Finding, FixSuggestion, QualityPage, QualitySection } from "../lib/types";

export const RUN = "r_20261026_101500_abc123";

export function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: "color-contrast:#subscribe",
    kind: "a11y",
    rule: "color-contrast",
    source: "axe",
    wcag: "1.4.3",
    level: "AA",
    impact: "serious",
    element_md: 4,
    selector: "#subscribe",
    frame_path: [],
    frame: null,
    element_desc: 'button "Subscribe"',
    message: "Text contrast 2.9:1 is below 4.5:1",
    measured: { contrast_ratio: 2.912, required: 4.5 },
    evidence: [],
    hints: [],
    help_url: "https://example.test/rules/color-contrast",
    ...overrides,
  };
}

export function fix(overrides: Partial<FixSuggestion> = {}): FixSuggestion {
  return {
    finding_ref: "color-contrast:#subscribe",
    property_or_attr: "color",
    current: "#9a9a9a",
    suggested: "#ffffff",
    changes: { color: "#ffffff", "background-color": "#2f6db3" },
    state: null,
    target_selector: "#subscribe",
    after_fix: { contrast_ratio: 5.31 },
    verified: true,
    method: "css_override",
    snippet: "#subscribe { color: #ffffff; background-color: #2f6db3; }",
    likely_file: "src/styles/buttons.css",
    rationale: "Darker background keeps the brand hue and reaches 4.5:1.",
    confidence: 0.92,
    ...overrides,
  };
}

export function page(overrides: Partial<QualityPage> = {}): QualityPage {
  return {
    kind: "a11y",
    source: "step",
    step: 2,
    url: "http://localhost:3000/signup",
    title: "Sign up",
    findings: [],
    fixes: [],
    not_scanned: [],
    visual: null,
    evidence: [],
    error: null,
    ...overrides,
  };
}

const imageAlt = finding({
  id: "image-alt:img.team",
  rule: "image-alt",
  wcag: "1.1.1",
  level: "A",
  impact: "critical",
  selector: "img.team",
  element_desc: "image",
  message: "Image has no alternative text",
  measured: null,
  help_url: null,
});

const imageAltFix = fix({
  finding_ref: "image-alt:img.team",
  property_or_attr: "alt",
  current: null,
  suggested: "Team photo",
  changes: { alt: "Team photo" },
  target_selector: null,
  after_fix: null,
  verified: false,
  method: "attribute",
  snippet: 'alt="Team photo"',
  likely_file: null,
  rationale: "Written from the file name; no vision model was used.",
  confidence: 0.4,
});

export function qualitySection(overrides: Partial<QualitySection> = {}): QualitySection {
  return {
    a11y: true,
    visual: true,
    fail_on: "serious",
    pages: [
      page({
        findings: [finding(), imageAlt],
        fixes: [fix(), imageAltFix],
        evidence: [`hercules://runs/${RUN}/quality/step-2/findings.json`, `hercules://runs/${RUN}/quality/step-2/annotated.png`],
      }),
      page({
        step: 4,
        url: "http://localhost:3000/welcome",
        title: "Welcome",
        findings: [finding({ id: "region:main", rule: "region", wcag: null, level: "none", impact: "moderate", selector: "main", element_desc: "main", message: "Content is not in a landmark", measured: null }), finding()],
        fixes: [fix()],
        not_scanned: ["iframe#chat"],
      }),
      page({
        kind: "visual",
        step: 4,
        url: "http://localhost:3000/welcome",
        title: "Welcome",
        visual: {
          baseline: "welcome-desktop",
          created: false,
          diff_ratio: 0.0231,
          threshold: 0.01,
          changed: true,
          regions: [[0, 0, 100, 40]],
          style_deltas: [{ key: "header", selector: "header .logo", property: "margin-left", before: "16px", after: "48px" }],
          missing: ["nav.primary"],
          classification: { label: "layout_break", confidence: 0.87, probabilities: { layout_break: 0.87, intended_change: 0.1, content_change: 0.03 }, engine: "local-llm" },
          images: {
            diff: `hercules://runs/${RUN}/quality/step-4/diff.png`,
            before: `hercules://runs/${RUN}/quality/step-4/before.png`,
            after: `hercules://runs/${RUN}/quality/step-4/after.png`,
          },
        },
      }),
    ],
    counts: { critical: 1, serious: 1, moderate: 1, minor: 0, findings: 3, fixes: 2, verified_fixes: 1, pages: 3 },
    ...overrides,
  };
}
