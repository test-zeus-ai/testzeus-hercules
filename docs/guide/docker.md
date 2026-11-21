# Running Hercules in Docker

> **Watch it:** [Run Hercules in Docker (2:02)](videos.md#14-run-hercules-in-docker).

The Hercules image holds everything a run needs: the `hercules` command, the daemon with
the tester UI, Playwright's Chromium with its fonts, and the nuclei scanner with its
templates. You mount your project folder into the container, and Hercules writes its cases,
runs and evidence there, as it does on your machine.

The model does not run in the image. Use a hosted OpenAI-compatible endpoint, or a model
server on your machine or network (see [Models and keys](#models-and-keys)).

## Get the image

Each release publishes the image for amd64 and arm64, with an SBOM and a signed build
attestation:

```bash
docker pull ghcr.io/test-zeus-ai/testzeus-hercules:latest     # or a version, such as :2.1.0
```

It is also on Docker Hub as `testzeus/hercules`. Check where an image was built with
`gh attestation verify oci://ghcr.io/test-zeus-ai/testzeus-hercules:latest --owner test-zeus-ai`.
The examples below call the image `hercules`; tag the pulled one that way
(`docker tag ghcr.io/test-zeus-ai/testzeus-hercules:latest hercules`) or build it yourself.

## Build the image

From a clone of the repository:

```bash
docker build -t hercules .
```

The build compiles the tester UI, installs the package with its locked dependencies,
installs Chromium, and downloads the pinned nuclei release (its checksum is checked) and
nuclei's templates. It needs internet access and takes a few minutes. The image is about
1.2 GB.

Build arguments:

| Argument | Default | Meaning |
|---|---|---|
| `UID`, `GID` | `1000` | User and group id Hercules runs as. See [Files in your project](#files-in-your-project). |
| `NUCLEI_TEMPLATES` | `true` | `false` leaves nuclei's templates out of the image (about 90 MB). Security scans then need `hercules scan --update-templates` first. |

The image builds for `linux/amd64` and `linux/arm64`. To build both at once, use a
builder that supports several platforms (for example the `docker-container` driver):

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t hercules .
```

## Start the tester UI

In your app's project folder:

```bash
docker run -d --name hercules \
  --shm-size=1g \
  -p 127.0.0.1:7373:7373 \
  -v "$PWD:/work" \
  -v hercules-home:/home/hercules/.hercules \
  -e OPENROUTER_API_KEY \
  hercules
```

- `-v "$PWD:/work"` mounts the project. The container's default command serves the project
  at `/work`.
- `-v hercules-home:...` keeps your Hercules home (user settings, the model server's
  checks, the scanner cache) in a named volume, so it survives a new container.
- `-p 127.0.0.1:7373:7373` lets only this machine open the UI. Keep both port numbers the
  same: the sign-in link names port 7373.
- `--shm-size=1g` gives Chromium enough shared memory. Docker's default of 64 MB can make
  the browser crash on large pages.

### Sign in

The UI needs a one-time sign-in link. The daemon prints one when it starts:

```bash
docker logs hercules
```

```text
Hercules daemon on http://127.0.0.1:7373 (project /work, 1 parallel run(s))
Open the UI once with: http://127.0.0.1:7373/login?code=...
```

Open the link in your browser. Each link works once and expires after 5 minutes. For a
new one:

```bash
docker exec hercules hercules ui --print
```

To use `localhost` instead, replace `127.0.0.1` with `localhost` in the link.

### Another port

To use another port, change it on both sides and in the command:

```bash
docker run -d --name hercules --shm-size=1g -p 127.0.0.1:8080:8080 \
  -v "$PWD:/work" -v hercules-home:/home/hercules/.hercules \
  hercules serve --project /work --host 0.0.0.0 --port 8080 --allow-remote
```

### Why the command says `--allow-remote`

The daemon listens on `0.0.0.0` inside the container, because a published port reaches
the container on its network address, not on its loopback address. Hercules refuses such
an address without `--allow-remote`, and then requires a strong API token in
`HERCULES_DAEMON_TOKEN`. The image makes a random token for each start when you set none.
It stays inside the container; your browser signs in with the one-time link instead.

The daemon accepts requests only for the host names `localhost`, `127.0.0.1` and those in
`HERCULES_DAEMON_ALLOWED_HOSTS`. It speaks plain HTTP. To open the UI from other machines,
put a TLS proxy in front of it, add the host name the browsers use to
`HERCULES_DAEMON_ALLOWED_HOSTS` (comma-separated), and set your own
`HERCULES_DAEMON_TOKEN` of at least 32 characters. Do not publish the port on a public
address without them.

## Run a case

The image's entry point is `hercules`, so arguments are a Hercules command:

```bash
docker run --rm --shm-size=1g \
  -v "$PWD:/work" -v hercules-home:/home/hercules/.hercules \
  -e OPENROUTER_API_KEY \
  hercules run .hercules/cases/sign-in.json
```

The exit code is Hercules' own (see [Running tests](running-tests.md)), so the command
works in CI. Other commands work the same way:

```bash
docker run --rm hercules version
docker run --rm -v "$PWD:/work" -v hercules-home:/home/hercules/.hercules hercules doctor
docker run --rm -it -v hercules-home:/home/hercules/.hercules hercules setup
```

`hercules run --headed` does not work in the container: there is no display. Use the
tester UI to watch a run.

## Docker Compose

The repository has an example `compose.yaml`:

```bash
HERCULES_PROJECT=/path/to/your/app docker compose up -d
docker compose logs hercules                   # the first sign-in link
docker compose exec hercules hercules ui --print
```

It publishes port 7373 on this machine only, mounts `HERCULES_PROJECT` (default: the
current folder) at `/work`, keeps the Hercules home in a volume, sets the shared memory
size, passes `OPENROUTER_API_KEY` from your shell by name, and reads more settings from
`hercules.env` when that file exists. It also maps `host.docker.internal` on Linux, so
the container can reach servers on your machine.

## Models and keys

Configure the model as you would without Docker: in `.hercules/config.yaml` of the project,
in the Hercules home, or with `HERCULES_LLM_*` variables. See
[Getting started](getting-started.md).

### Keys

Keys stay in environment variables; the settings only name the variable. Pass each key by
name, so its value comes from your shell and not from the command line:

```bash
export MODEL_API_KEY=...       # or load it from a file
docker run ... -e MODEL_API_KEY -e OPENROUTER_API_KEY hercules
```

Docker records these values in the container's settings, so `docker inspect` shows them.
To avoid that, put the keys in a file of `NAME=VALUE` lines and mount it under
`/run/secrets/` with a name ending in `.env`. The image reads every such file when it
starts and exports its variables; it never runs the file.

```bash
docker run ... -v "$HOME/.config/hercules/keys.env:/run/secrets/keys.env:ro" hercules
```

With Compose, use a secret (see the commented lines in `compose.yaml`). The file must be
readable by the user Hercules runs as. Optional quotes and Windows line ends are removed.
Names that change how programs run (`PATH`, `HOME`, `LD_*`, `PYTHON*`, `HERCULES_HOME`,
proxies, ...) are ignored with a warning: the file is for keys only.

The image never contains a key. Do not add keys as build arguments.

### What protects the keys

The keys and the daemon's token are in the Hercules process. The browser and the security
scanner get only what they need to run (the path, the locale, proxies), so a page or a
template cannot read them from their environment.

Chromium runs without its own sandbox in the container, as Playwright starts it there, so
the container is the boundary: a page that broke out of the browser could read anything
the `hercules` user can, keys included. Test only apps you trust with those keys, keep
the defaults (no `--privileged`, no Docker socket mounted, no `--network host`), and use
keys made for testing, with spending limits.

### A model server on your machine

Inside the container, `127.0.0.1` is the container itself. Reach a model server on your
machine as `host.docker.internal` (Docker Desktop provides it; on Linux add
`--add-host=host.docker.internal:host-gateway`, or use `compose.yaml`). The server must
listen on an address the container can reach, not only on `127.0.0.1` (for Ollama,
`OLLAMA_HOST=0.0.0.0`).

Hercules counts only the container's own loopback address as local. List the host so it
counts as local, which the `local` egress policy requires:

```yaml
# .hercules/config.yaml
llm:
  provider: ollama
  base_url: http://host.docker.internal:11434/v1
egress:
  policy: local
  local_hosts: [host.docker.internal]
```

### Telemetry

Telemetry is off in the image: it sets `HERCULES_TELEMETRY=0`. Environment variables take
precedence over the config files, so to turn it on, pass `-e HERCULES_TELEMETRY=1`
(events then go to PostHog, as described in the guide). See
[Modes and privacy](modes-and-privacy.md#telemetry).

## The app under test

Give the case a base URL the container can reach:

- An app on your machine: `http://host.docker.internal:3000`. With Docker Desktop this
  reaches an app that listens on `127.0.0.1` too.
- An app in another container: put both on one Docker network and use the container's
  name, for example `http://shop:8000` (`docker run --network ...`, or a service in the
  same Compose file).
- An app on the internet or your network: its usual URL.

The URL is often different from the one you use outside the container. Keep it in a saved
environment, or pass `--base-url` to `hercules run`.

## Files in your project

Hercules writes `.hercules/` in the mounted project as the user it runs as, uid 1000 by
default.

- **Docker Desktop (macOS, Windows):** the files belong to you, whatever the uid.
- **Linux:** the files get uid 1000. If your user has another id, build the image with
  yours, so the files belong to you:

  ```bash
  docker build --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" -t hercules .
  ```

  Rootless Docker and Podman map the container's users to yours already.

The Hercules home volume belongs to the same uid. After you rebuild with another uid, use
a new volume (or remove the old one).

## Security scans

nuclei and its templates are in the image, so scans and security steps work as they are
(see [Security scans](security-scan.md)). To get newer templates:

```bash
docker exec hercules hercules scan --update-templates
```

The update lives in that container. For a lasting update, rebuild the image.

A scan sends many requests in a short time. When the app runs on your machine and the
container reaches it through `host.docker.internal`, Docker's port forwarding can refuse
some of them, and the scan ends with "the app stopped answering". Lower `rate_limit`, or run
the app in a container on the same Docker network as Hercules.

## Mobile

**Mobile web** works in the image as it is: name a device in the case's environment, for
example `"device": "iPhone 15"`. See [Mobile testing](mobile-testing.md#mobile-web).

**Native apps** need an Appium server with Android or iOS tools and a device, emulator or
simulator. Those do not go in the image. Run Appium on your machine and point Hercules at it:

```bash
appium --address 0.0.0.0 --port 4723     # on your machine
```

```bash
docker run ... -e HERCULES_APPIUM_URL=http://host.docker.internal:4723 hercules
```

Appium on another machine counts as remote. List its host under `egress.local_hosts`, as for
a model server, or the `local` egress policy refuses it:

```yaml
egress:
  local_hosts: [host.docker.internal]
```

Device clouds (BrowserStack, Sauce Labs) need only their keys, passed like model keys.

## Troubleshooting

| Problem | What to do |
|---|---|
| The sign-in link does not open | Use the port you published, the same on both sides. Get a new link with `docker exec <container> hercules ui --print`: each link works once, for 5 minutes. |
| `forbidden host` | You opened the UI with a host name other than `localhost` or `127.0.0.1`. Add it to `HERCULES_DAEMON_ALLOWED_HOSTS`, and read [why the command says --allow-remote](#why-the-command-says---allow-remote). |
| `refusing to bind` or `needs a token` | You replaced the default command. Keep `--allow-remote` with `--host 0.0.0.0`, and leave `HERCULES_DAEMON_TOKEN` unset or set it to 32 characters or more. |
| The browser crashes or pages go blank | Add `--shm-size=1g` (Compose: `shm_size`). |
| The app or model server is not reachable | Use `host.docker.internal`, not `127.0.0.1`, and check that the server listens on an address the container can reach. On Linux add `--add-host=host.docker.internal:host-gateway`. |
| A local model server is refused under the `local` policy | List its host in `egress.local_hosts`. |
| `Permission denied` in `/work` or in the home volume | The container's uid differs from the owner. Build with your `UID` and `GID`, and use a new home volume. |
| The container reports unhealthy | The health check asks the daemon. A container that runs one command instead of the daemon has none, so it reports unhealthy; that is expected. |
| A scan ends with "the app stopped answering" | See [Security scans](#security-scans). |

`docker exec <container> hercules doctor` checks the settings, the model server, Appium and
the scanner from inside the container.
