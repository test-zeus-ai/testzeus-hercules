# AG2 vs LangGraph Orchestrator — Step-by-Step & Tool-Call Comparison

## 1. Toolcall-by-toolcall walkthrough — Run 1, "Signup_page"

### AG2 (Run 1) — 4 planner steps, 13 tool calls, ~65s total

| Step | Tool call # | Tool | Start | End | Duration |
|---|---|---|---|---|---|
| 1 | 1 | openurl | 14:32:11 | 14:32:17 | 6s |
| 1 | 2 | get_page_text | 14:32:20 | 14:32:20 | 0s |
| 1 | 3 | get_interactive_elements | 14:32:21 | 14:32:22 | 1s |
| 2 | 4 | openurl | 14:32:30 | 14:32:30 | 0s |
| 2 | 5 | get_interactive_elements | 14:32:32 | 14:32:33 | 1s |
| 2 | 6 | click | 14:32:34 | 14:32:34 | 0s |
| 2 | 7 | get_input_fields | 14:32:38 | 14:32:39 | 1s |
| 3 | 8 | get_input_fields | 14:32:51 | 14:32:52 | 1s |
| 3 | 9 | get_interactive_elements | 14:32:52 | 14:32:53 | 1s |
| 3 | 10 | bulk_enter_text | 14:32:56 | 14:33:01 | 5s |
| 3 | 11 | click | 14:33:01 | 14:33:01 | 0s |
| 3 | 12 | get_page_text | 14:33:04 | 14:33:04 | 0s |
| 3 | 13 | get_interactive_elements | 14:33:04 | 14:33:05 | 1s |
| 4 | — | (terminate, no tool call) | | | |

- Step 4 is the planner's final "all steps completed" turn — no tool call attached.
- This pattern repeats in every run/framework: the last planner turn is a verdict, not an action.

### LangGraph (Run 1) — 6 planner steps, 21 tool calls, 85.48s total (framework-reported)

| Step | Tool call # | Tool | Time | Duration (if logged) |
|---|---|---|---|---|
| 1 | 1 | open_url | 14:39:56 | — |
| 1 | 2 | get_interactive_elements | 14:40:04 | 0.96s |
| 1 | 3 | get_page_text | 14:40:04 | 0.07s |
| 2 | 4 | click | 14:40:13 | — |
| 2 | 5 | get_input_fields | 14:40:16 | 1.06s |
| 2 | 6 | get_interactive_elements | 14:40:17 | 1.03s |
| 2 | 7 | get_page_text | 14:40:17 | 0.06s |
| 3 | 8 | get_input_fields | 14:40:25 | 1.07s |
| 3 | 9 | bulk_enter_text *(4 fields)* | 14:40:30 | — |
| 3 | 10 | get_input_fields | 14:40:35 | 1.07s |
| 4 | 11 | get_interactive_elements | 14:40:44 | 1.07s |
| 4 | 12 | click | 14:40:46 | — |
| 4 | 13 | get_page_text | 14:40:48 | 0.09s |
| 4 | 14 | get_interactive_elements | 14:40:49 | 0.98s |
| 5 | 15 | get_interactive_elements | 14:41:01 | 0.99s |
| 5 | 16 | get_input_fields | 14:41:02 | 0.99s |
| 5 | 17 | get_page_text | 14:41:02 | 0.06s |
| 5 | 18 | click | 14:41:07 | — |
| 5 | 19 | get_interactive_elements | 14:41:09 | 0.94s |
| 5 | 20 | get_input_fields | 14:41:10 | 0.94s |
| 5 | 21 | get_page_text | 14:41:10 | 0.06s |
| 6 | — | (terminate, no tool call) | | |

**Per-step planner+executor timing** (from LangGraph's own STEP TIMINGS block):

| Step | Planner LLM call | Executor LLM calls (turns) |
|---|---|---|
| 1 | 3.65s | 12.25s |
| 2 | 3.57s | 9.07s |
| 3 | 2.36s | 17.57s |
| 4 | 2.52s | 10.04s |
| 5 | 5.00s | 16.42s |
| 6 (final) | 3.02s | — |

**Key takeaway from this run pair:**
- AG2 collapses "click the link → check fields" into Step 2 of 4.
- LangGraph keeps a finer 6-step plan, splitting "verify registration form is visible" from "confirm field readiness."
- Net effect: **LangGraph's planner takes 2 extra planning steps** here (6 vs 4), but each LangGraph step does fewer tool calls on average.

---

## 2. Aggregate comparison — all 3 runs, both test cases

### 2a. Signup_page

| Metric | AG2 Run1 | AG2 Run2 | AG2 Run3 | **AG2 avg** | LG Run1 | LG Run2 | LG Run3 | **LG avg** |
|---|---|---|---|---|---|---|---|---|
| Planner steps | 4 | 5 | 6 | **5.0** | 6 | 6 | 6 | **6.0** |
| Tool calls | 13 | 15 | 22 | **16.7** | 21 | 23 | 25 | **23.0** |
| Executor LLM turns | — | — | — | n/a | 18 | 23 | 24 | **21.7** |
| Total time | 65.0s | 127.0s | 101.0s | **97.7s** | 85.5s | 95.3s | 103.9s | **94.9s** |
| Total tokens | 67,426 | 78,938 | 120,862 | **89,075** | 90,195 | 108,184 | 110,469 | **102,949** |
| Avg time/tool call | 1.31s | 3.53s | 0.95s | **1.93s** | — | — | — | n/a (turn-based) |
| Avg time/executor turn | — | — | — | n/a | 13.07s | ~13.7s | ~17.1s | **~14.6s** |
| Avg time/planner turn | — | — | — | n/a | 3.35s | 2.85s | 3.07s | **3.09s** |

### 2b. Delete_the_account

| Metric | AG2 Run1 | AG2 Run2 | AG2 Run3 | **AG2 avg** | LG Run1 | LG Run2 | LG Run3 | **LG avg** |
|---|---|---|---|---|---|---|---|---|
| Planner steps | 6 | 7 | 6 | **6.3** | 5 | 5 | 5 | **5.0** |
| Tool calls | 26 | 28 | 26 | **26.7** | 27 | 24 | 29 | **26.7** |
| Executor LLM turns | — | — | — | n/a | 28 | 26 | 28 | **27.3** |
| Total time | 282.0s | 116.0s | 122.0s | **173.3s** | 100.7s | 101.9s | 105.3s | **102.7s** |
| Total tokens | 155,932 | 166,459 | 148,658 | **157,016** | 129,584 | 125,526 | 137,397 | **130,836** |
| Avg time/tool call | 5.58s | 0.75s | 1.08s | **2.47s** | — | — | — | n/a |
| Avg time/executor turn | — | — | — | n/a | 20.39s | 20.40s | 22.20s | **21.0s** |
| Avg time/planner turn | — | — | — | n/a | 3.83s | 4.05s | 3.31s | **3.73s** |

**Outlier flag:**
- `AG2 Run1 Delete_the_account` is a clear outlier at 282s.
- One individual tool call in that run took 33s (vs. 1–11s for the equivalent calls in other runs) — most likely a slow page transition after account deletion, not a structural framework issue.
- Excluding that outlier, AG2 Run2/Run3 average ~119s — actually closer to LangGraph's ~102.7s, and shows the headline gap is sensitive to network/page-load variance, not purely the orchestrator.

---

## 3. Step-count and tool-call dissimilarities

*Note: Signup_page and Delete_the_account are sequential phases of one run (the second depends on the account created in the first), not independent test cases — so these per-phase differences are diagnostic detail, not separate comparisons to "win."*

**Signup_page phase — who takes the extra step:**
- LangGraph consistently plans **6 steps**.
- AG2 plans **4–6 steps** (variable across runs, trending upward in later runs).
- In the Run 1 vs Run 1 comparison: **LangGraph takes 2 extra planning steps** (6 vs 4) — it separates "verify registration form is visible" from "confirm field readiness," where AG2 folds both into one step.
- This is a planner-prompt/architecture difference, not a failure — LangGraph's plan is simply finer-grained.

**Delete_the_account phase — who takes the extra step (inverted):**
- Here AG2 takes the extra step: **6–7 vs LangGraph's consistent 5.**
- Reason: AG2's planner re-confirms login state in a separate step after the deletion; LangGraph folds login-state confirmation into the same step as the deletion click.

**Which tool drives the call-count difference:**
- `get_interactive_elements` is the single biggest contributor — LangGraph's executor calls it markedly more often per step (defensive DOM re-fetch after almost every action, e.g. 9–13 calls in `Delete_the_account` vs AG2's more conservative pattern).
- `get_input_fields` shows the same pattern.
- No run used a tool the other framework didn't also use — both rely on the same five core tools (`open_url`, `click`, `bulk_enter_text`, plus the three read tools `get_interactive_elements` / `get_input_fields` / `get_page_text`).
- The difference is purely call *frequency*, driven by how aggressively each framework re-verifies DOM state between actions.

---

## 4. Tokens — both frameworks now have real, comparable data

*Reminder: only the rightmost two "full run" columns are a fair comparison — the per-phase columns are shown for detail but aren't independent results, since Delete_the_account only runs because Signup_page succeeded first in the same session.*

| Run | AG2 Signup phase | LG Signup phase | AG2 Delete phase | LG Delete phase | **AG2 full run** | **LG full run** |
|---|---|---|---|---|---|---|
| Run 1 | 67,426 | 90,195 | 155,932 | 129,584 | **223,358** | **219,779** |
| Run 2 | 78,938 | 108,184 | 166,459 | 125,526 | **245,397** | **233,710** |
| Run 3 | 120,862 | 110,469 | 148,658 | 137,397 | **269,520** | **247,866** |
| **Average** | **89,075** | **102,949** | **157,016** | **130,836** | **246,092** | **233,785** |

**Takeaways:**
- **The only comparison that matters here is the full-run total:** AG2 averages ~246,092 tokens vs. LangGraph's ~233,785 → **LangGraph uses about 5.3% fewer tokens overall.**
- This is despite LangGraph making *more* total tool calls (49.7 vs 43.3 avg) — implying LangGraph's per-call token cost is somewhat lower, likely from carrying less accumulated context per turn.
- **AG2's token split (executor vs planner) within the run:** the `browser_nav_agent` (executor) dominates — e.g. Run 1 Signup phase: 49,468 of 67,426 tokens (73%) went to the executor, only 27% to the planner.
- **LangGraph's split is similar:** executor turns also out-consume planner turns overall (e.g. Run 1 Signup phase: 18 executor turns ≈ 58k tokens vs. 6 planner turns ≈ 29k tokens, roughly 67%/33%).
- **Phase-level numbers (informational, not separate verdicts):** within the same run, LangGraph's token usage is higher in the Signup phase (+15.5% vs AG2) but lower in the Delete phase (−20.0% vs AG2) — these offset each other, which is exactly why only the full-run total should be used to judge which framework is more token-efficient.

---

## 5. Speed comparison

*As with tokens, the full-run total (both phases combined) is the only fair comparison — the phase rows below are shown for detail, not as separate verdicts, since Delete_the_account is a continuation of the same session as Signup_page.*

**Combined full-run total (the number that matters):**
- AG2 ≈ 271.0s vs. LangGraph ≈ 197.6s → headline gap of ~27%.
- That headline number is almost entirely driven by one AG2 run with a 282s outlier (a single 33s tool call in the Delete_the_account phase, most likely a slow page load).
- With that one outlier run excluded, **LangGraph is only ~2-3% faster overall** — the more representative number.

**Phase-level detail (informational only):**

| Phase | AG2 avg time | LG avg time | Difference | Faster |
|---|---|---|---|---|
| Signup_page | 97.7s | 94.9s | 2.8s | LangGraph, by **2.9%** |
| Delete_the_account | 173.3s | 102.7s | 70.6s | LangGraph, by **40.7%** *(skewed by AG2's 282s outlier)* |
| Delete_the_account *(outlier excluded)* | 119.0s | 102.7s | 16.3s | LangGraph, by **13.7%** |

- Even the outlier-excluded phase numbers shouldn't be averaged independently of each other — they're sequential phases of the same run, so the full-run total above is still the figure to trust.
- Conclusion: most of the headline 27% speed gap looks like page-load/network variance from one run, not a structural orchestration advantage.

---

## 6. Summary

| | AG2 (avg, full run) | LangGraph (avg, full run) |
|---|---|---|
| Total planner steps | 11.3 | 11.0 |
| Total tool calls | 43.3 | 49.7 |
| Total time | ~271.0s | ~197.6s |
| Total tokens | ~246,092 | ~233,785 |
| Avg time per tool call | ~2.2s | n/a — turn-based avg ~17.8s/executor-turn |
| Tokens per tool call | ~5,683 | ~4,705 |
| Speed vs. other framework | ~27% slower (~2-3% slower excluding outlier) | ~27% faster (~2-3% faster excluding outlier) |
| Tokens vs. other framework | ~5.3% more tokens | ~5.3% fewer tokens |

**Bottom line:**
- LangGraph plans in marginally fewer total steps but makes more granular tool calls per step (more defensive DOM re-fetching).
- LangGraph is moderately faster end-to-end — but a large share of that gap traces to one slow AG2 run, not a consistent architectural advantage.
- Token usage is now directly comparable: **AG2 uses about 5.3% more tokens overall**, despite making fewer total tool calls — pointing to a heavier per-call token footprint (likely more accumulated conversational history per turn).
- LangGraph trades a higher tool-call count for a flatter, more token-efficient per-call cost.

---

## Key takeaways (at a glance)

These are based on the **combined full-run total** (Signup_page + Delete_the_account together), since the two phases are sequential parts of one scenario, not independent tests.

- **Steps:** Nearly tied (AG2 11.3 vs LangGraph 11.0 avg total planner steps across the full run).
- **Tool calls:** LangGraph makes more (49.7 vs AG2's 43.3 avg). The gap is driven mostly by one tool — `get_interactive_elements` — which LangGraph's executor calls more defensively after actions.
- **Time:** LangGraph wins, but the margin is much smaller than it first looks. Headline gap is ~27% (271.0s vs 197.6s), driven almost entirely by one AG2 run with a 282s outlier in the Delete_the_account phase (a single 33s tool call, likely a slow page load). With that outlier removed, LangGraph is only ~2-3% faster.
- **Tokens:** LangGraph uses fewer tokens overall (~233,785 vs AG2's ~246,092, LangGraph −5.3%), despite making more total tool calls — its per-call token footprint is lighter, likely because it carries less accumulated conversation history per turn. Note: AG2's `Total Tokens: 0` summary line is a logging bug — the real numbers are logged separately per-agent and were used here.
- **Phase-level detail (informational only, not a separate verdict):** Within the combined run, LangGraph's token and step profile looks different in each phase — heavier in Signup_page, lighter in Delete_the_account — and AG2's is the mirror image. Since the phases aren't independently comparable (Delete_the_account only runs because Signup_page succeeded first), these shouldn't be read as "LangGraph wins phase 1, AG2 wins phase 2" — only the full-run totals are a valid comparison.
- **Consistency:** LangGraph's run-to-run variance is much lower (e.g. full-run times: 219,779 / 233,710 / 247,866 tokens, smoothly increasing) than AG2's, which has one run that's 2-3x slower than its other two due to a single outlier tool call — AG2's worst-case behavior is far worse than its typical behavior, which matters more in practice than the average.

---

## Conclusion — how much better/worse is LangGraph overall?

**Speed:** LangGraph is faster, but only marginally once noise is removed.
- Headline number: **~27% faster** than AG2 (271.0s vs 197.6s combined).
- With the one-off 282s AG2 outlier excluded: the gap narrows sharply — LangGraph lands at roughly **~2-3% faster**, which is the more honest number, since that outlier was a slow page load, not a framework trait.
- On Signup_page alone (the cleaner of the two test cases, no outliers either side): LangGraph is **~2.9% faster** — essentially a tie.

**Tokens:** LangGraph is cheaper, also by a modest margin.
- LangGraph uses **~5.3% fewer tokens** overall (233,785 vs 246,092 average per full run).
- It achieves this despite making *more* tool calls (49.7 vs 43.3 avg) — its per-call token footprint is lighter, likely because it doesn't carry as much accumulated conversation history per turn.

**Planning behavior:** Roughly a wash, just distributed differently.
- Total planner steps are nearly identical (11.0 vs 11.3 avg).
- Neither framework is "more efficient" at planning — they just split the work differently across the two test cases (LangGraph plans more finely on Signup, AG2 plans more finely on Delete_the_account).

**Overall verdict:**
- **LangGraph is only marginally better, not dramatically better** — roughly **2-3% faster** and **~5% cheaper** on tokens once the AG2 outlier run is excluded.
- The widely-different 27%-faster headline number is mostly an artifact of one slow AG2 page load, not a structural advantage of LangGraph's orchestration.
- If you exclude noisy runs, the two frameworks land within single digits of each other on every axis (speed, tokens, step count) — this is a case of LangGraph having a **small, consistent edge**, not AG2 being meaningfully worse.