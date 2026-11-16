# Exploratory testing

`hercules explore` looks for bugs nobody wrote a test for. You say where to look, for how long
and with how many explorers; Hercules splits the app into charters, explores them in real
browsers, and reports only what it could reproduce from a fresh start. Each finding comes with
its steps, the evidence and a failing test case you can keep.

Explore only apps you own, in a test environment. Explorers fill in forms, click buttons and
change data. They never sign out, pay, delete an account, send messages or do anything else on
the forbidden list (see [Scope and safety](#scope-and-safety)).

## Walkthrough videos

Five short videos, with a spoken walkthrough, show each part of exploratory testing, recorded against Acme Pantry, the demo
shop in `bench/explore` with its seeded bugs switched on. Each video plays on this page (the MP4
files are in `docs/assets/videos/explore`). The steps under each one do the same on your machine. Finding ids differ on every run: use the ones
your session prints.

### Set up the demo

1. Start the shop with every seeded bug on. Its sign-in page takes any name.

   ```bash
   uv run python bench/explore/app.py --bugs all --port 8200
   ```

2. In your project, save a case that signs in, as `.hercules/cases/sign-in.json`:

   ```json
   {
     "id": "sign-in",
     "title": "Sign in to Acme Pantry",
     "environment": {"base_url": "http://127.0.0.1:8200"},
     "steps": [
       {"action": "Open \"/login\""},
       {"action": "Fill \"ana\" in \"Name\" and click the \"Sign in\" button"}
     ]
   }
   ```

3. Tell Hercules the cart's rules, as `.hercules/explore/promises.yaml` (see
   [What counts as wrong](#what-counts-as-wrong)):

   ```yaml
   - id: total-not-negative
     kind: not_negative
     route: /cart
     target: '#total'
     text: The cart total is never negative
   - id: badge-equals-count
     kind: equal
     route: /cart
     target: '#badge'
     other: '#count'
     text: The cart badge equals the items in the cart
   ```

4. Start the daemon and open the tester UI: `hercules serve`, then `hercules ui`.

### 1. Start a session and watch it live

https://github.com/user-attachments/assets/d9d6c42f-8884-4292-b149-f3ef1fe52437

<sub>[Download the MP4](../assets/videos/explore/01-start-and-watch-live.mp4)</sub>

1. Open **Explore** and click **Start exploring**.
2. Under **Where**, choose **Start case** and pick `sign-in.json`.
3. Keep the mission **P1 hunt**. Set **Time** to 6 minutes, **Explorers** to 3 and
   **Reproducers** to 2, and read the estimate under the form.
4. Click **Start**. The session page shows the charters, each explorer's browser live, and
   **Clues → findings**: a clue becomes a finding only once it reproduces from a fresh start.
5. When the time is up, the page says how many findings wait for your decision. Click
   **Review findings**.

### 2. Review findings in one click

https://github.com/user-attachments/assets/00034a15-2847-495c-9096-45e5512fd7a8

<sub>[Download the MP4](../assets/videos/explore/02-review-findings-one-click.mp4)</sub>

1. Open **Explore → Findings**. The tab counts the findings to review, and those come first.
   Each row shows the real error under its title.
2. Click a finding to see the expected and actual result, how often it reproduced, the
   evidence runs and the steps.
3. Click **Real bug**. Your name comes from git `user.name`, and the finding's case is kept as
   a known-bug test. **Not a bug**, **Can't reproduce** and **Duplicate** ask why first.
4. Click **Copy as Github issue** (or Jira, Linear, Markdown) to paste it into your tracker.
5. Decide the rest. The toolbar shows the share of decided findings that were real bugs.

### 3. Explore from the terminal

https://github.com/user-attachments/assets/90c9a356-633a-4898-997c-cb4809ec4037

<sub>[Download the MP4](../assets/videos/explore/03-explore-from-the-terminal.mp4)</sub>

1. Start a 3-minute session aimed at one form, and answer `y` to the estimate:

   ```bash
   hercules explore start --from .hercules/cases/sign-in.json -m forms --prompt "break the profile form" -t 3 -w 2
   ```

2. The command follows the session live: charters, clues and findings. A bug found before is
   marked `seen_again`, not reported twice.
3. Look around with any unique start of an id:

   ```bash
   hercules explore sessions
   hercules explore findings
   hercules explore show f779
   hercules explore issue fb1a --tracker github
   ```

### 4. Fix, reverify, keep it fixed

https://github.com/user-attachments/assets/c22e9d17-ae41-4e2d-bd16-85c96d018732

<sub>[Download the MP4](../assets/videos/explore/04-fix-reverify-regression.mp4)</sub>

1. Fix a bug. In the demo, restart the shop without the empty-search bug (B7):

   ```bash
   uv run python bench/explore/app.py --bugs B1,B2,B3,B4,B5,B6,B8 --port 8200
   ```

2. Rerun every kept bug case against the new build. A fixed bug's case passes and moves to
   `verify_fixed`; the others still fail:

   ```bash
   hercules explore reverify
   ```

3. Close the verified fix. Its case becomes a regression test that fails a run if the bug comes
   back:

   ```bash
   hercules explore triage fe13 --state closed
   hercules explore findings --state all
   ```

### 5. Explore from your coding agent

https://github.com/user-attachments/assets/ce9222d4-33aa-4918-a893-5c8bde477547

<sub>[Download the MP4](../assets/videos/explore/05-explore-from-your-coding-agent.mp4)</sub>

1. Give your agent the Hercules tools. This writes `.mcp.json`, the `hercules-test` skill and
   the `qa-runner` subagent (use `cursor`, `copilot`, `codex` or `gemini` for other agents):

   ```bash
   hercules init --agent claude
   ```

2. Start the agent and ask: "Explore the shop for bugs for 3 minutes, starting from the sign-in
   case. Then tell me what you found." It starts a session, follows it and reports each finding
   with its steps and a link to it in the tester UI.
3. Decide in your own words: "Those are real bugs. Confirm them for me, and draft a GitHub issue
   for the worst one." The agent records the decision as yours, via the coding agent (see
   [Coding agents](#coding-agents)).

## Start a session

```bash
hercules explore start --base-url http://localhost:3000 --time 30m --workers 2
```

`--time` takes `30m`, `1h` or `1h30m`; a bare number is minutes, and a session runs at least a
minute. Short forms: `-t 30m -w 2 -m forms`.

Before it starts, Hercules shows how many explorers and reproducers will run, for how long, and
about how many model decisions that takes (with a cost estimate for a hosted model). Press Enter
to go, or pass `--yes`. The command follows the session to the end, printing each charter, clue
and finding (with its id) as it happens and a progress line when nothing has happened for a
minute. At the end it says how many findings wait for your decision and the commands to decide
them. Pass `--no-wait` to get the session id back at once. A session may run a few minutes past
`--time`: clues found near the end still get reproduced.

`--dry-run` runs recon only and prints the charters a full session would start with.

To start in a signed-in state, give a case that signs in. Each explorer runs it once and starts
from where it ends:

```bash
hercules explore start --from .hercules/cases/sign-in.json --time 1h --workers 4
```

You can also start from a saved environment (`--env staging`). The same session starts from the
tester UI (**Explore → Start exploring**) and from a coding agent (the `hercules_explore` tool).

### Missions

A mission says what to look for. The default is `p1`.

| Mission | Looks for |
|---|---|
| `p1` | Bugs at P1 or worse: lost or corrupted data, money taken wrongly, another user's data shown, a core flow blocked |
| `forms` | Every form with empty, long, malformed, negative and unusual values |
| `changed` | Only what changed: give `--changed /cart,/checkout` or `--since main` (the files changed since a git ref) |
| `smoke-plus` | A quick pass over every page with plain moves |
| `a11y` | What a keyboard-only or screen-reader user cannot reach, read or operate |
| `conditions` | Narrow screens, offline moments, expired sessions |
| `persona:<name>` | The app as an `impatient`, `careful`, `confused` or `screen-reader` user would use it |

`--prompt "find ways the cart total can be wrong"` replaces the mission's target with your own.
A project mission is a YAML file at `.hercules/explore/missions/<name>.yaml` with `target`,
`levers`, `severity_floor` and `lever_weights`.

### Swarm size

`--workers` is how many explorers run at once, `--reproducers` how many reproducers confirm clues
at once. The size is yours: Hercules never raises or lowers it. If it is more than this machine
can hold (more than `explore.max_workers`, more browsers than the daemon runs at once, or more
memory than the machine has), the start is refused with the numbers; pass `--allow-more-workers`
to go ahead anyway. You can change the size of a running session; the change applies to the next
charters and is logged:

```bash
hercules explore size --workers 6            # the latest session
hercules explore size x_20261113_10 --workers 6
```

Every command that takes a session accepts its full id, a unique start of it, or `last`, and
uses the latest session when you leave it out. `hercules explore sessions` lists them.

With a model on your machine, explorers wait for each other's decisions, so more explorers mostly
help with a hosted model.

## How a session works

1. **Setup.** The start case runs once per explorer and saves the signed-in state.
2. **Recon.** One explorer walks the app with plain moves (links and buttons) to learn its pages,
   for 10% of the session (at least 90 seconds, at most 40% of a short session).
3. **Plan.** Code lists the charters the mission allows over the pages recon saw, one sentence
   each, such as "Explore /cart with out-of-order moves", with a time box. When the charters run
   out and explorers have found pages the plan did not know, it plans again from those pages;
   a charter that already ran is not planned again.
4. **Explore.** A free explorer takes the next charter. A model picks the charter with the most
   risk not covered yet, and in each turn picks one move from those code offers:
   - **inputs:** empty, long, negative, emoji, quotes and similar values in each field;
   - **order:** double submit, back, reload;
   - **conditions:** a narrow window, a moment offline;
   - **time:** waiting, a lost session;
   - **people:** keyboard-only use.

   After every move, checks look for something wrong.
5. **Reproduce.** Each clue is replayed from a fresh start without a model, three times by
   default; at least two must show it again. A fresh start means a clean browser: the start
   case's recorded steps sign in again, so nothing explorers left in their session leaks in.
   - If the clue's own moves miss, the reproducer tries again with the session's earlier moves
     first (from other charters in the same signed-in session). This catches bugs that need
     state an earlier charter built, such as an item in the cart. The finding then says so.
   - A control whose name holds a count, such as "Cart (3)", is matched with whatever count the
     fresh session shows. The finding's steps name it without the count.

   The steps are cut down to the fewest that still show it. A model then replays the shortened
   steps as plain-language steps. A clue that reproduces becomes a **finding**. A clue that does
   not becomes "not reproduced" or "flaky" in the report, never a finding.
6. **Wrap up.** The report is written, and every finding is added to the project's
   [findings ledger](#triage-the-findings-ledger).

A promising clue also queues a **dig** charter: a short, high-priority look around the same spot.

### What counts as wrong

| Kind | Checks |
|---|---|
| Code | A page error (an uncaught exception), a console error, a 5xx response, a failed request, a broken link, a button that does nothing |
| Promises | Something the app should always do, checked after every move on its route |
| Judged | A model reads the page before and after the move and says whether the result is wrong |

Things already wrong on the happy path (during setup and recon) are recorded once as known
noise and not reported again. A judged finding on its own is capped at P2.

Promises live in `.hercules/explore/promises.yaml`:

```yaml
- id: total-not-negative
  kind: not_negative          # text_visible, text_absent, equal, not_negative, persist
  route: /cart
  target: "#total"
  text: The cart total is never negative
- id: badge-equals-count
  kind: equal
  route: /cart
  target: "#badge"
  other: "#count"
  text: The cart badge equals the items in the cart
```

Promises Hercules learns during a session are listed in the report for you to review: a learned
promise can encode a bug as correct behaviour. Copy the right ones into the file.

## The report

When a session ends, its report is in `.hercules/explore/<session>/report.md` (and `report.json`
for tools), in the tester UI, and from `hercules explore report` (the latest session) or
`hercules explore report <session>`. It has:

- the findings by severity, with steps, expected and actual results, and how often each one
  reproduced;
- one debrief per charter: what it tried, what it saw, what it did not get to, and how its time
  was split;
- the clues that did not reproduce, the known noise and any UX notes (design preferences, not
  bugs);
- the **coverage gaps**: the pages seen, the levers not used on each, and the charters still to
  run. Coverage describes what Hercules did, never a percentage of the app;
- the actions the scope guard blocked;
- the severity rubric the calls were made against.

**No findings is not a pass.** It means nothing reproducible was found in that time, with that
coverage.

Each finding also comes with a test case that replays it (status `known_bug`). Its run fails while
the bug is there (`hercules run` still exits 0 for it, with a note) and passes once the bug is
fixed. See [Keeping the cases](#keeping-the-cases): not every case is worth keeping, so a person
decides.

## Triage: the findings ledger

Severities in a report are the model's proposals until a person confirms them. The ledger
(`.hercules/explore/ledger.json`) keeps every finding across sessions, keyed by its signature:

- the same bug found again is **seen again** on that build, not a new finding. A console message
  is one finding whichever page shows it;
- a finding you dismissed (by design, won't fix, known issue, duplicate) stays quiet;
- a closed finding that comes back is a **regression**.

In the tester UI (**Explore → Findings**), the tab shows how many findings wait for review, the
ones waiting come first, and each one opens with one-click decisions: **Real bug**, **Not a bug**,
**Can't reproduce** and **Duplicate** (which suggests findings with the same message). The last two
ask why. Severity, tracker key and an explicit case decision are under **More options**. A finished
session's page links straight to its findings and has **Run again** with the same settings.

From the command line:

```bash
hercules explore findings                    # waiting ones show the next command
hercules explore findings --state open
hercules explore show f3a91c07de1
hercules explore triage f3a91 --state confirmed --severity P1
hercules explore triage f3a91 --tracker SHOP-412
hercules explore triage f77d0 --state by_design --reason "limit is documented"
hercules explore issue f3a91 --tracker jira
hercules explore issue f3a91 --repository acme/shop    # a prefilled GitHub new-issue link
```

A finding id can be shortened to any unique start of it. `--by` defaults to your git
`user.name`. Every change is recorded with who made it and why. A dismissal (`by_design`,
`wont_fix`, `cannot_reproduce` or `duplicate` with `--duplicate-of`) needs a reason. Hercules
measures its real-bug rate (precision) as confirmed findings ÷ decided findings, so findings
nobody decided never count in its favour.

When the daemon serves another project, `findings`, `show`, `issue`, `sessions`, `status` and
`report` read this project's files instead, so you can still look at them.

## Keeping the cases

A finding's case is proposed first. A person then keeps it, turns it into a regression test or
rejects it. One finding has one case, however many sessions find it again.

| Case | Where it is | What it does |
|---|---|---|
| Proposed | `.hercules/explore/cases/<finding>.json` | Waits outside your cases: nothing runs it until someone decides |
| Kept | `.hercules/cases/explore-<finding>.json`, status `known_bug` | One of your cases: runs report it but never fail on it while the bug is open |
| Regression | `.hercules/cases/explore-<finding>.json`, status `ready`, tag `regression` | One of your cases: a run fails if the bug comes back |
| Rejected | `.hercules/explore/cases/rejected/<finding>.json` | Never run, and not proposed again while the finding stays rejected |

Triage decides the case unless you choose: confirming a finding (or filing it in a tracker)
keeps its case, dismissing it rejects its case, and closing it after a verified fix makes its case
a regression test. Choose yourself in the tester UI (the **Case** field) or with `--case`:

```bash
hercules explore triage f3a91 --case kept
hercules explore triage f3a91 --state closed          # after a verified fix: the case becomes a regression test
hercules explore triage f77d0 --case rejected --reason "a third-party script, not our app"
hercules explore findings --case kept
```

A rejected case needs a reason. A case becomes a regression test only once its bug is fixed
(the finding is in "verify fix" or closed): a regression case must pass, so making one from an
open bug would fail every run. A case you edited after keeping it stays as you edited it when a
later session finds the same bug.

`hercules explore reverify` runs the kept and regression cases on the current build. A kept case
that passes moves its finding to "verify fix", and a person closes it, which turns the case into a
regression test. A regression case that fails reopens its finding, and the command exits 1, so it
can gate a CI job. A run that ends any other way (an error, a step waiting for approval, cancelled, or
over `--timeout`, 15 minutes per case by default) is inconclusive: it changes nothing and the command
exits 2. `--finding ID` (repeatable) reruns only those findings; `--json` prints the result as JSON.
The daemon runs the cases (`POST /api/explore/reverify`), so the command, the tester UI and a coding
agent all get the same result.

## Scope and safety

- **Hosts.** Explorers only reach the base URL's host, plus any you add with `--allow-hosts`. Pages still load their own images, styles and scripts from other hosts (a CDN, for example), as they do for every visitor; explorers never open a page on, or send anything that changes data to, a host outside the list. A page's background calls to such hosts (analytics, push notifications, ads) get an empty answer, so nothing leaves. A script error raised only by another host's script (a push or analytics SDK) is not reported as a finding about your app.
  Every other request is blocked. A host with no `staging`, `test`, `qa`, `dev`, `local` or
  similar label is refused unless you pass `--i-own-this-environment`.
- **Paths.** `--deny-paths /admin,/billing` keeps explorers away from those paths.
- **Forbidden actions.** Controls named pay, purchase, buy now, delete account, send, email,
  invite, share, transfer, withdraw, unsubscribe, sign out, log out, change role or reset password
  are never clicked. `--forbid` adds words to the list and `--allow` takes words off it for one
  session. `--ask` asks a person instead of skipping. DELETE requests are always blocked.
- **Rate.** Writes are spaced out (5 per second by default).
- **Isolation.** `--isolation` decides how explorers avoid each other's data:
  - `serial-writes` (the default): one charter that changes data at a time;
  - `partition`: each explorer gets its own part of the app;
  - `accounts`: one account per explorer; `--accounts` names their variables;
  - `single`: one explorer at a time.

  `--teardown` runs a case at the end to clean up.

## Stop conditions and exit codes

A session stops when any of these happens:

- its time is up (`--time`);
- the model cost limit is reached (`--max-cost`). A hosted model that reports tokens but no dollars is priced at `explore.cost_per_decision_usd` per call, and the cost then shows with a `~`;
- the number of findings you asked for is reached (`--target`);
- several charters in a row add no coverage;
- you stop it: `hercules explore stop` (the latest session), `hercules explore stop <session>` or **Stop** in the UI. Ctrl+C on `hercules explore start` only stops following; the session keeps running, and the message says how to follow or stop it.

Reproductions still running when it stops get a short grace period.

| Exit code | Meaning |
|---|---|
| 0 | Finished, and no new or regressed finding at or above the severity floor gates it |
| 1 | A new or regressed finding at or above the floor: confirmed by a person, or with `--gate-on proposed-hard` also a proposed one backed by a code check or a specified promise |
| 2 | Refused or failed: no place to explore, a production-looking host, a size over the guard rail, an error |
| 3 | The time or cost ran out before recon finished |

## Coding agents

A coding agent explores with these MCP tools (see [Using Hercules from coding agents](coding-agents.md)):

- `hercules_explore` starts a session and returns its id; `hercules_explore_stop` ends it early;
- `hercules_explore_status` reports progress, waiting up to 50 seconds per call, with the latest
  events and a `next` hint;
- `hercules_explore_report` returns a compact summary once the session ends (`full` adds the markdown);
- `hercules_explore_sessions`, `hercules_explore_findings` and `hercules_explore_finding` read the
  sessions and the findings ledger, with each finding's steps, evidence and audit trail;
- `hercules_explore_triage` records your triage decision, `hercules_explore_issue` writes a finding as
  an issue, and `hercules_explore_reverify` reruns the kept cases after a fix.

An agent uses the swarm size you asked for, or the defaults, and the instructions `hercules init`
writes tell it to ask you before a session over 30 minutes or over 2 explorers. A size over the
guard rail is refused with the numbers; only you can start it anyway, from the UI or the command
line. A host that does not look like a test environment is refused until you tell the agent it is
yours.

Agents triage only on your explicit word. `hercules_explore_triage` needs your name and your words
deciding the change; it is recorded as "<name> via coding agent" with your words in the audit trail,
and the same workflow rules apply as in the UI. The instructions tell the agent to show you each
finding and never to triage on its own judgement.

## Settings

```yaml
explore:
  workers: 2                   # explorers when you give no --workers
  reproducers: 1
  max_workers: 8               # the guard rail; more needs --allow-more-workers
  default_time_s: 1800
  cost_per_decision_usd: 0.0005  # price of one hosted model call: the estimate, and the session cost when the provider reports no dollars
```

## Files

```text
<project>/.hercules/explore/
  ledger.json                 every finding, its triage state and history
  promises.yaml               promises you specify
  missions/<name>.yaml        project missions
  <session>/session.json      the session's last state
  <session>/report.md         the report (report.json for tools)
<project>/.hercules/explore/cases/<finding>.json             proposed cases (rejected/ holds rejected ones)
<project>/.hercules/cases/explore-<finding>.json              kept and regression cases
<project>/.hercules/runs/<run_id>/explore/                    each explorer's and reproducer's moves, clues and debrief
```

## Measuring it

`bench/explore/` holds a small shop with seeded bugs (`app.py`, listed in `bugs.yaml`) and a
scorer:

```bash
python bench/explore/run_bench.py --time 10m --workers 2
python bench/explore/run_bench.py --clean --time 5m
```

It prints recall (seeded bugs found ÷ seeded bugs on), precision (findings that match a seeded
bug ÷ findings) and, with `--clean`, the false findings on a build with no bugs.
