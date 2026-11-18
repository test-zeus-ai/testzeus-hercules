# Quick start: the tester UI

> **Watch it:** [From an empty project to a passing test (1:52)](videos.md#2-from-an-empty-project-to-a-passing-test).

A few steps from an empty project to a test case that passes, all in the browser. Each
step links to the full description.

## Open the UI

1. In your project folder, set the secrets your cases need in the shell, then start the
   daemon:

   ```bash
   export APP_TEST_PASSWORD=...
   hercules serve
   ```

   The daemon reads secrets from the environment it was started in, so set them first
   (restart the daemon after changing them).
2. In a second terminal, open the UI:

   ```bash
   hercules ui
   ```

   It opens a one-time sign-in link in your browser (valid for 5 minutes; `--print` only
   prints it).

   The UI opens on the **Dashboard**:
   - runs waiting for your answer (an approval or a plan to review);
   - the last 7 days next to the week before: runs, pass rate, median duration and model cost;
   - runs per day over the last 14 days;
   - what is running and queued now;
   - the latest runs;
   - a summary of your cases, environments, model and connected clouds, each linking to its page.

   On a new project it shows the first steps instead. **Start a run** is there too. The full
   list of runs is under **Runs**.
3. Open **Models** and check that the local model server is reachable and which run modes
   are available. **Run doctor** checks it again. Hybrid mode needs its key variable set
   (see [Modes and privacy](modes-and-privacy.md)).

## Write a test case

4. Open **Environments**, select **New environment** and give it a name, the app's base URL
   (for example `http://localhost:3000`), the viewport and the names of the secrets its
   cases expect. Values are never stored here.
5. Open **Cases** and start a new case:
   - **Form**: a title, the environment, then one step per line of what a tester would
     do (`Click the "Sign in" button`), each with an optional expected outcome (`I should
     see "Welcome back"`). Under **Test data**, add values for `{{placeholders}}`; pick
     **Environment variable** for a password or token so the case holds only its name.
   - **Gherkin**: paste or type one scenario. `Then` lines become checks. Its test data
     (and secret references) is kept beside the file in `<name>.data.json`; edit it in the
     **Form** tab. See [Writing Gherkin](gherkin.md) for quoting, `@env:` and the editor's
     suggestions and keys.

   The editor checks the case while you type and highlights what is wrong. **TestCase
   JSON** shows the file that will be saved. See [Writing tests](writing-tests.md).
6. Select **Save**. The case is written to `.hercules/cases/`.

## Run it

7. Select **Run** on the case (or **Start a run** on the Dashboard or the Runs page and pick the saved
   case). In the dialog choose the environment and the mode (**Auto** uses the mode from
   your settings, else hybrid when its key is set, else pure; see
   [Modes and privacy](modes-and-privacy.md)). Tick **Show the browser window** to watch
   the real browser.
   **Advanced** has the quality checks (accessibility, visual), the human-in-the-loop
   options (confirm destructive clicks, ask when a step fails) and plan review.
8. Select **Start run**. The run page opens with the live view, the step timeline and the
   decision inspector. Press **Space** to pause and resume, **N** to run one step at a
   time (see [Debugging a run](running-tests.md#debugging-a-run-in-the-ui)).
9. If the run asks a question (a drafted case, a plan to review, a destructive click, a
   step that could not be done), answer it on the card: **Approve**, **Reject** or **Edit
   step…** (see [Approvals](running-tests.md#approvals)).

## Finish the case

10. When the run fails, open the failed step. **Expected** and **Actual** say what did not
    hold, the screenshot (**Marked** shows what the model could choose from) shows the
    page, and the classification says what kind of failure it looks like: a product bug,
    a fragile test, test data, the environment, and so on. Everything the run saw is under
    `.hercules/runs/<run_id>/`.
11. Fix the case in **Cases** (usually a step's wording or an expected outcome), save it,
    and select **Re-run** on the run page. A re-run reads the case file again, so your
    edits are used; it opens the start dialog so you can change options first.
12. A case is done when:
    - it passes, and passes again on a second run;
    - every step that matters has an expected outcome, so the run checks it rather than
      only clicking through;
    - passwords, tokens and sign-in links come from environment variables, not the case;
    - it is **Ready**, not a draft.
13. Optionally, open the passed run and select **Export as code** to keep it as a
    Playwright test in your repository (see [Exporting runs as code](exporting-code.md)).
