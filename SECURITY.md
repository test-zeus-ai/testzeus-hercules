# Security policy

## Supported versions

Security fixes go into the latest release. Upgrade to it with `uvx testzeus-hercules@latest`,
`uv tool upgrade testzeus-hercules` or `pip install -U testzeus-hercules`, or pull the newest image.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through GitHub: on the repository's
**Security** tab, choose **Report a vulnerability**. Include what you found, how to reproduce it,
and the version (`hercules version`).

We acknowledge a report within three working days, keep you informed while we fix it, and credit
you in the release notes unless you prefer otherwise.

## Scope

In scope: the `testzeus-hercules` package, the `hercules` daemon and tester UI, the exported test
code it writes, and the published Docker image. Out of scope: the apps you test with Hercules, and
the model providers and device clouds you connect it to.

Hercules is built to run on your own machine: the daemon listens on 127.0.0.1 and asks for a sign-in
link, secrets are referenced by environment variable name and kept out of evidence and exports, and
the security scanner only targets the app under test. A way around any of these is a vulnerability.
