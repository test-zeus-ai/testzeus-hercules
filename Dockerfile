# syntax=docker/dockerfile:1.7
#
# Hercules, by TestZeus: the CLI, the daemon with its tester UI, Playwright's Chromium and
# the nuclei scanner in one image. See docs/guide/docker.md.
#
#   docker build -t hercules .
#   docker run --rm -it --shm-size=1g -p 127.0.0.1:7373:7373 -v "$PWD:/work" hercules
#
# Build arguments:
#   UID, GID          user and group id of the user Hercules runs as (default 1000:1000).
#                     Set them to your own (`id -u`, `id -g`) on Linux, so the files Hercules
#                     writes into the mounted project belong to you. UID 0 (root) is refused.
#   NUCLEI_TEMPLATES  "true" (default) downloads nuclei's templates into the image, so
#                     security scans work without a download at run time. "false" skips them.
#                     They are the latest release at build time, so two builds can differ;
#                     the build prints their version, and `nuclei -templates-version` shows it.

# Base images, pinned by version and digest (multi-arch indexes: amd64 and arm64).
ARG NODE_IMAGE=node:22.23.4-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d
ARG PYTHON_IMAGE=python:3.13.18-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.19@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6

FROM ${UV_IMAGE} AS uv

# ---- The tester UI: plain files, the same for every platform, so it builds on the build machine's own.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY ui/ ./
# Writes the bundle to /src/testzeus_hercules/ui/static (see ui/vite.config.ts).
RUN npm run build && test -f /src/testzeus_hercules/ui/static/index.html

# ---- The Python package and its dependencies, in a virtual environment at /opt/hercules.
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/hercules
WORKDIR /src
# Dependencies first, so a source change does not reinstall them. No dev tools; the `api`
# extra adds JSONPath and OpenAPI response checks for API steps.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra api --no-install-project
COPY LICENSE CHANGELOG.md THIRD_PARTY_NOTICES.md MANIFEST.in ./
COPY testzeus_hercules ./testzeus_hercules
COPY --from=ui /src/testzeus_hercules/ui/static ./testzeus_hercules/ui/static
# --reinstall-package: uv's cache keys a local project on pyproject.toml, not on its sources.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra api --no-editable --reinstall-package testzeus-hercules \
    && /opt/hercules/bin/hercules version

# ---- The runtime image.
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/opt/hercules/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    HOME=/home/hercules \
    HERCULES_HOME=/home/hercules/.hercules \
    HERCULES_TELEMETRY=0 \
    HERCULES_DAEMON_ALLOWED_HOSTS=localhost

# tini reaps the browser's child processes and forwards stop signals. libatomic1: the Node that Playwright
# (1.65 and later) bundles to drive the browser needs it, and the slim image does not have it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/hercules /opt/hercules

# Chromium's headless shell (all a container uses) with its system libraries and fonts.
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /tmp/*

# The pinned nuclei release, checksum checked by `hercules scan --install`, on PATH.
RUN HERCULES_HOME=/tmp/hercules-install hercules scan --install \
    && install -m 0755 "$(find /tmp/hercules-install/cache/nuclei -type f -name nuclei)" /usr/local/bin/nuclei \
    && rm -rf /tmp/hercules-install \
    && nuclei -version 2>&1 | grep -q "Engine Version"

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/hercules-entrypoint
COPY --chmod=0755 docker/healthcheck.py /usr/local/bin/hercules-healthcheck

# Declared this late so changing them reuses the layers above.
ARG UID=1000
ARG GID=1000
ARG NUCLEI_TEMPLATES=true
# A user of its own, never root. `-o` lets GID match an existing group (for example 20 on macOS hosts).
RUN test "${UID}" != 0 || { echo "UID 0 would run Hercules as root; build with a user's id" >&2; exit 1; } \
    && groupadd -o -g "${GID}" hercules \
    && useradd -o -u "${UID}" -g "${GID}" -m -d /home/hercules -s /usr/sbin/nologin hercules \
    && mkdir -p /work /home/hercules/.hercules \
    && chown -R "${UID}:${GID}" /work /home/hercules \
    && chmod 0700 /home/hercules/.hercules

USER hercules
# nuclei's templates go to ~/nuclei-templates (its own default); `hercules scan --update-templates` refreshes them.
RUN if [ "${NUCLEI_TEMPLATES}" = "true" ]; then \
        HERCULES_HOME=/tmp/hercules-templates hercules scan --update-templates \
        && test -d "$HOME/nuclei-templates" && rm -rf /tmp/hercules-templates \
        && nuclei -templates-version 2>&1 | tail -n 1; \
    fi

WORKDIR /work
VOLUME ["/home/hercules/.hercules"]
EXPOSE 7373

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["hercules-healthcheck"]

ARG VERSION=2.0.0.dev0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Hercules" \
      org.opencontainers.image.description="Hercules, by TestZeus: plain-language end-to-end tests of web apps, APIs and mobile apps, run locally." \
      org.opencontainers.image.vendor="TestZeus" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/test-zeus-ai/testzeus-hercules" \
      org.opencontainers.image.url="https://github.com/test-zeus-ai/testzeus-hercules" \
      org.opencontainers.image.documentation="https://github.com/test-zeus-ai/testzeus-hercules/blob/main/docs/guide/docker.md" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENTRYPOINT ["/usr/bin/tini", "--", "hercules-entrypoint"]
# The daemon and tester UI for the project mounted at /work, reachable through a published port.
CMD ["serve", "--project", "/work", "--host", "0.0.0.0", "--port", "7373", "--allow-remote"]
