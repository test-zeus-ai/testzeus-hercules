# Showcase: from a plain-language test to BrowserStack, Sauce Labs and TestMu AI

This example runs one plain-language case against the public Swag Labs demo shop, then runs
the test Hercules exported from it on every connected cloud. Cloud browsers run on the
internet, so the app under test must be reachable from it: Swag Labs is.

## In the tester UI (for a live demo)

1. `pip install 'testzeus-hercules[cloud]'`. For Sauce Labs web tests, also
   `npm i -g saucectl`.
2. `hercules serve`, then `hercules ui`.
3. **Integrations**: paste the user name and access key of each cloud. Each card turns green
   and says what it can run here.
4. **Cases**: create a case from `swag-labs-sign-in.feature` (or paste its steps), with the
   base URL `https://www.saucedemo.com`, and run it. Watch it pass in the live view.
5. On the passed run, choose **Export as code** to show the Playwright code Hercules wrote.
   Then choose **Run on a cloud**: pick TestMu AI, Edge on macOS, and run it.
6. The **Cloud runs** panel follows it to *passed*. **Open session** shows the cloud's
   recording of the same test.
7. Do the same with BrowserStack (Chrome on Windows) and Sauce Labs, then use
   **Download project for … CI** to show the ready-made `browserstack.yml`,
   `.sauce/config.yml` or `hyperexecute.yaml`.

## From the command line

```bash
examples/clouds/demo.sh              # every connected cloud
examples/clouds/demo.sh browserstack # one cloud
```

The script runs the case, runs the exported test on each connected cloud (`hercules cloud
run`), and writes each cloud's CI project next to it (`hercules cloud bundle`). Its exit
code is `0` when every cloud run passed.

See [Running tests on clouds](../../docs/guide/clouds.md).
