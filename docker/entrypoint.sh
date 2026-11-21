#!/bin/sh
# Entry point of the Hercules image: runs `hercules` with the arguments given.
#
# Keys from files: every /run/secrets/*.env file (a Docker or Compose secret) is read as
# NAME=VALUE lines and exported, so model keys need not be passed with -e, where
# `docker inspect` would show them. Lines that are empty, comments (#) or not NAME=VALUE
# are skipped; the file is read, never run. Optional quotes around a value and a Windows
# line end are removed. Names that change how programs run (PATH, HOME, LD_*, PYTHON*,
# HERCULES_HOME, ...) are refused: a secrets file holds keys, not configuration.
#
# `hercules serve --allow-remote` (the image's default command) needs a strong API token in
# HERCULES_DAEMON_TOKEN. When none is set, a random one is made for this start. It stays
# inside the container: clients in it read it from daemon.json, and the browser signs in
# with a one-time link instead.
set -eu

CR="$(printf '\r')"

load_env_file() {
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%"$CR"}"
        line="${line#export }"
        case "$line" in
            '' | '#'*) continue ;;
        esac
        name="${line%%=*}"
        value="${line#*=}"
        [ "$name" != "$line" ] || continue
        case "$name" in
            '' | [0-9]* | *[!A-Za-z0-9_]*) continue ;;
            PATH | HOME | USER | SHELL | IFS | ENV | BASH_ENV | TMPDIR | LD_* | DYLD_* | PYTHON* | HERCULES_HOME | PLAYWRIGHT_* | SSL_CERT_* | *_PROXY | *_proxy)
                echo "hercules: ignoring $name in $1 (only keys go in a secrets file)" >&2
                continue
                ;;
        esac
        case "$value" in
            \"*\")
                value="${value#\"}"
                value="${value%\"}"
                ;;
            \'*\')
                value="${value#\'}"
                value="${value%\'}"
                ;;
        esac
        export "$name=$value"
    done <"$1"
}

for file in "${HERCULES_SECRETS_DIR:-/run/secrets}"/*.env; do
    [ -f "$file" ] && [ -r "$file" ] && load_env_file "$file"
done

if [ "${1:-}" = "serve" ] && [ -z "${HERCULES_DAEMON_TOKEN:-}" ]; then
    HERCULES_DAEMON_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
    export HERCULES_DAEMON_TOKEN
fi

exec hercules "$@"
