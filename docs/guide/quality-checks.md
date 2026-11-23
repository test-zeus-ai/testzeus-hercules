# Visual and accessibility checks

> **Watch it:** [Visual and accessibility checks (1:50)](videos.md#12-visual-and-accessibility-checks).

Hercules can check more than whether a flow works: whether pages look right and whether
they are accessible. These checks run on the same pages your test cases visit and report
their findings with the run's evidence.

Code measures everything: axe-core (bundled, works offline) and Hercules' own checks for
accessibility, computed styles and element boxes for layout, a perceptual pixel diff for
visual baselines. Every finding names the element (a selector you can use in the browser's
dev tools) and, where there is one, comes with a concrete fix value: the nearest colour
that passes contrast, the size a tap target needs, an `aria-label`, a focus outline, the
style value a regressed element had in the baseline. Each fix is applied to the live page
and the check is run again before it is reported, so `verified: true` means "this value,
applied in the page, resolved the finding".

There are two ways to use the checks:

- **Quality steps**: a step in a case that checks the page at that point and passes or
  fails like any other step.
- **Run audits**: run any case with `a11y` and/or `visual` turned on, and every page it
  reaches is audited. Findings are reported; they fail the run only if you ask for that.

## Quality steps

Write the check in plain language, or give the step a structured `quality` payload.

```gherkin
Feature: Checkout

  Scenario: Checkout is accessible and unchanged
    Given I open "/checkout"
    Then the page should have no accessibility violations
    And the checkout page should look like the baseline
    And nothing should overlap or be cut off on mobile and desktop
```

The model reads each such line into the structured payload once, and the check itself runs
in code. The reading is saved in the step's `plan.jsonl`. Phrasings that work:

| You write | Check |
|---|---|
| `the page should have no accessibility violations` | `{"kind": "a11y", "fail_on": "minor"}` |
| `there should be no serious accessibility issues` | `{"kind": "a11y", "fail_on": "serious"}` |
| `the page should meet WCAG AAA` | `{"kind": "a11y", "level": "AAA"}` |
| `all images should have alt text` | `{"kind": "a11y", "rules": ["image-alt"]}` |
| `the "#signup" form should have no accessibility violations` | `{"kind": "a11y", "scope": "#signup", "fail_on": "minor"}` |
| `the checkout page should look like the baseline` | `{"kind": "visual", "baseline": "checkout"}` |
| `the dashboard should look the same as before, ignoring ".clock"` | `{"kind": "visual", "baseline": "dashboard", "mask": [".clock"]}` |
| `nothing should overlap or be cut off on mobile` | `{"kind": "layout", "viewports": [375]}` |

Use the `Then` form (a check, not an action): a step starting with the verb "Check", such
as `Check the page for accessibility problems`, can be read as ticking a checkbox.

The structured form, in a JSON case:

```json
{"action": "The checkout is accessible", "quality": {"kind": "a11y", "fail_on": "serious"}},
{"action": "The checkout looks as before", "quality": {"kind": "visual", "baseline": "checkout", "mask": [".clock", "#ads"], "key_elements": ["#pay", "h1"]}},
{"action": "Nothing overlaps on phones", "quality": {"kind": "layout", "viewports": [375], "scope": "main"}}
```

The fields are listed in [Writing tests](writing-tests.md#accessibility-visual-and-layout-steps).

### Verdicts

| Check | Passed | Failed | Unverified |
|---|---|---|---|
| `a11y`, `layout` | No finding at or above `fail_on` (lower ones are still reported) | At least one finding at or above `fail_on` | never |
| `visual` | The page matches the baseline: pixels within `threshold` and the key elements' styles unchanged | The decider judged the difference a layout break (or its impact reaches `fail_on`) | The baseline did not exist and was created from this page; or the page differs and was judged an intended or content change; or no decider could look at it |

`fail_on` is an impact: `critical`, `serious` (the default), `moderate` or `minor`
(`minor` fails on any finding). An unverified step fails the run like a failed one, so a
page never passes a visual check against a baseline that was only just created.

## Accessibility

The audit runs axe-core with the WCAG 2.0, 2.1 and 2.2 A and AA rules (AAA rules too at
`level: AAA`) in every same-origin frame and shadow root, plus Hercules' own checks:

| Check | What is measured | Fix value |
|---|---|---|
| Contrast | Text and background colours from computed styles (through transparent layers) | The nearest colour, same hue, that meets 4.5:1 (3:1 for large text; 7:1 / 4.5:1 at AAA) |
| Accessible names | Buttons, links and form controls without a name | An `aria-label` from the control's icon, classes or the model |
| Target size | Controls smaller than 24 × 24 CSS px without enough spacing | `min-width` / `min-height` and padding |
| Alt text | Images without `alt` | Alt text written by the local vision model from the image crop |
| Keyboard | Controls Tab never reaches, focus traps, focus that is not visible | `tabindex`/`role`, or a focus outline with enough contrast |

Alt text needs a local model that can see images. Without one, the fix is a placeholder
from the file name, marked `verified: false` with a note to replace it: the attribute
resolves the rule, but nobody has checked that the words describe the image.

Frames from another origin cannot be scanned; they are listed as `not_scanned` on the
page's result rather than silently skipped.

If the case's `source.files_changed` lists the files a change touched (coding agents fill
it in), each fix names the `likely_file` that holds the element.

## Visual baselines

A visual check stores a baseline under `.hercules/baselines/<name>.png` and `<name>.json`
(the screenshot, the masked regions and the computed styles of the key elements). The
first run creates it; later runs compare against it:

1. **Pixels**: both screenshots are downscaled and blurred slightly so anti-aliasing does
   not count, masked regions are left out, and the share of changed pixels is compared
   with `threshold` (default 0.001, that is 0.1%).
2. **Styles**: every computed-style property of each key element is compared with the
   baseline (`font-size 18px -> 14px on #cta`), plus its position and size.
3. **Classification**: when anything differs, the decider looks at the before,
   after and diff images with the style changes and says whether it is an intended change,
   a layout break or a content change. In hybrid mode JEV answers; in pure mode the local
   model does. A layout break is a `serious` finding, the other two are `minor`. The
   classification needs a decider that can see screenshots (pure mode with an
   image-capable model, or hybrid mode with egress `hybrid`); without one the step is
   `unverified`.

For every changed property Hercules proposes the baseline value as a fix and verifies it
by applying all of them and comparing again.

The before, after and diff images are saved in the step's evidence
(`steps/<n>/quality/<name>.before.png`, `.after.png`, `.diff.png`; changed pixels are red,
masked regions grey). To accept a new look, delete `.hercules/baselines/<name>.png` and
`<name>.json`; the next run creates the baseline again. Commit the baselines directory if
your team shares them.

Keep the viewport the same between runs (`environment.viewport`): a baseline captured at
one size never matches another.

## Layout

A layout check resizes the viewport to each width (375, 768 and 1280 CSS px by default,
the original size is restored afterwards) and finds overlapping controls, text cut off by
its container and controls that fall off the screen. Each finding comes with the CSS
change that resolved it in the page (letting a flex row wrap, removing a negative
margin, letting text wrap, a `max-width` or `min-width`), or the first candidate, marked
not verified, when none did.

## Run audits

Turn the audits on for a whole run:

```bash
hercules run checkout.json --a11y --visual
```

- In the tester UI: the **Accessibility** and **Visual** toggles in the start-run dialog.
- Through the daemon API: `"a11y": true`, `"visual": true` in `POST /api/runs`.
- From a coding agent: `a11y` and `visual` on `hercules_run`.

Each page state a browser step leaves behind is audited once: the URL, the title, the main
headings and any open dialog identify the state, so a step that does not change the page
does not audit it again. `a11y` runs the accessibility audit without the keyboard walk
(pressing Tab moves focus and could change what the next step sees; use an `a11y` step for
it). `visual` compares the page with a baseline named after the case and the page
(`<case id>.<page path>.<hash>`), created on the first run, and checks the layout at the
three default widths.

Audits never fail a step. The run fails on them only when you set `fail_on`; the run's
hints then say how many findings reached it. A problem while auditing a page (a page that
navigated away, a timeout after 180 s) is recorded on that page and the run goes on.

`fail_on` is an impact: `critical`, `serious`, `moderate` or `minor`. A run audit finding
at or above it fails the run (`minor` fails on any finding). It has no default for run
audits: without it, findings are only reported. Set it:

- With `hercules run`: `--quality-fail-on serious`. It needs `--a11y` or `--visual`;
  the run then exits with `1` when a finding reaches the gate.
- In the tester UI: the **Fail the run on** select in the start-run dialog.
- Through the daemon API: `"fail_on": "serious"` in `POST /api/runs`.
- From a coding agent: `fail_on` on `hercules_run`.

```bash
hercules run checkout.json --a11y --quality-fail-on serious
```

This gate applies only to run audits. A quality step has its own `fail_on` in its payload
(default `serious`) and fails its own step, whatever the run's gate.

## Results

Findings land in `run.json` under `quality`:

```json
"quality": {
  "a11y": true, "visual": false, "fail_on": null,
  "counts": {"critical": 1, "serious": 2, "moderate": 0, "minor": 1, "findings": 4, "fixes": 3, "verified_fixes": 3, "pages": 2},
  "pages": [
    {
      "kind": "a11y", "source": "run", "step": 1, "url": "http://localhost:3000/signup", "title": "Sign up",
      "findings": [{"id": "color-contrast:4f1c2a9b0e", "rule": "color-contrast", "wcag": "1.4.3", "level": "AA", "impact": "serious",
                    "selector": "#subscribe", "element_desc": "button \"Subscribe\"", "message": "...", "measured": {"ratio": 3.19, "required": 4.5}}],
      "fixes": [{"finding_ref": "color-contrast:4f1c2a9b0e", "property_or_attr": "background-color", "current": "#4a90e2", "suggested": "#3278c9",
                 "snippet": "#subscribe { background-color: #3278c9; }", "verified": true, "after_fix": {"ratio": 4.5},
                 "likely_file": "src/components/SubscribeButton.tsx"}],
      "evidence": ["hercules://runs/<run_id>/steps/1/quality/run-a11y.json", "hercules://runs/<run_id>/steps/1/quality/run-a11y.annotated.png"]
    }
  ]
}
```

- `source` is `step` for a quality step and `run` for a run audit; `step` is the step that
  ran the check, or after which the page was audited.
- A finding's `id` is stable for the same rule on the same element, so the same problem
  seen on several pages is counted once in `counts`.
- `snippet` is ready to paste: a CSS rule for style fixes, attributes for HTML fixes.
- Evidence is under `.hercules/runs/<run_id>/steps/<n>/quality/`: the findings as JSON, a
  screenshot with a numbered box per finding (numbered in the order of the page's
  `findings`), and the visual before, after and diff images.
- The run summary (`hercules run` output, `summary` in the API) adds a line with the
  counts and the top fixes.

**Tester UI**: the run page shows a Quality panel with the counts, the findings grouped by
impact, each with its fix value, a verified badge and a copy button for the snippet, the
visual comparisons with before, after and diff images and the style changes, and the
annotated screenshots.

**Coding agents**: `hercules_run_result` adds the counts and the top three fixes with their
verified values and inlines the worst finding's annotated screenshot (or visual diff);
`hercules_quality_report` lists every finding with its fixes, filtered by `min_impact` and
`kind`. See [Coding agents](coding-agents.md).
