#!/usr/bin/env bash
set -e

###############################################################################
# Parse input parameter: --browser=chrome or --browser=none
###############################################################################
BROWSER_CHOICE="none"

for arg in "$@"; do
  case $arg in
    --browser=chrome)
      BROWSER_CHOICE="chrome"
      shift
      ;;
    --browser=none)
      BROWSER_CHOICE="none"
      shift
      ;;
    *)
      # Ignore any other args
      ;;
  esac
done

###############################################################################
# 1) Identify OS (Mac or Linux) and locate Chrome
###############################################################################
OS_TYPE="$(uname -s)"
CHROME_BIN=""

if [ "$OS_TYPE" = "Darwin" ]; then
  # Mac
  # Typical Chrome path
  if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
    CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  elif command -v google-chrome >/dev/null 2>&1; then
    CHROME_BIN="$(command -v google-chrome)"
  elif command -v chrome >/dev/null 2>&1; then
    CHROME_BIN="$(command -v chrome)"
  fi
else
  # Linux
  if command -v google-chrome >/dev/null 2>&1; then
    CHROME_BIN="$(command -v google-chrome)"
  elif command -v chrome >/dev/null 2>&1; then
    CHROME_BIN="$(command -v chrome)"
  fi
fi

###############################################################################
# 2) Pull the Hercules Docker image
###############################################################################
echo "Pulling the latest testzeus/hercules Docker image..."
docker pull testzeus/hercules:latest

###############################################################################
# 3) (Optional) Launch Chrome in CDP mode (or skip) with extra configurations
###############################################################################
CHROME_ARGS=(
  "--remote-debugging-port=9222"
  "--user-data-dir=/tmp/chrome-test-profile"
  "--disable-session-crashed-bubble"
  "--disable-notifications"
  "--no-sandbox"
  "--disable-blink-features=AutomationControlled"
  "--disable-infobars"
  "--disable-background-timer-throttling"
  "--disable-popup-blocking"
  "--disable-backgrounding-occluded-windows"
  "--disable-renderer-backgrounding"
  "--disable-window-activation"
  "--disable-focus-on-load"
  "--no-first-run"
  "--no-default-browser-check"
  "--window-position=0,0"
  "--disable-web-security"
  "--disable-features=IsolateOrigins,site-per-process"
)

case "$BROWSER_CHOICE" in
  chrome)
    if [ -n "$CHROME_BIN" ]; then
      echo "Launching Google Chrome (requested) at: $CHROME_BIN"
      "$CHROME_BIN" "${CHROME_ARGS[@]}" &> /dev/null &
      sleep 5
    else
      echo "Chrome was requested but not found. Skipping browser launch."
    fi
    ;;
  none)
    echo "Browser launch skipped as requested."
    ;;
  *)
    echo "Unrecognized or no browser choice. Skipping browser launch."
    ;;
esac

###############################################################################
# 4) Create .env if it doesn't exist, then set CDP_ENDPOINT_URL
###############################################################################
if [ ! -f .env ]; then
  echo "No .env file found. Creating one from .env-copy (if exists)..."
  if [ -f .env-copy ]; then
    cp .env-copy .env
  else
    echo "CDP_ENDPOINT_URL=http://host.docker.internal:9222" >> .env
    echo ".env file created with default CDP_ENDPOINT_URL."
  fi
  echo "Please review/edit the newly created .env file for environment variables."
fi

# Inject or update the CDP_ENDPOINT_URL in .env
if grep -q '^CDP_ENDPOINT_URL=' .env; then
  sed -i.bak 's|^CDP_ENDPOINT_URL=.*|CDP_ENDPOINT_URL=http://host.docker.internal:9222|' .env
else
  echo "CDP_ENDPOINT_URL=http://host.docker.internal:9222" >> .env
fi

###############################################################################
# 5) Run Hercules in Docker with .env, agents_llm_config.json, and opt mounted
###############################################################################
echo "Running testzeus/hercules:latest Docker container..."
docker run --env-file=.env \
  -p 9222:9222 \
  -v "$(pwd)/agents_llm_config.json:/testzeus-hercules/agents_llm_config.json" \
  -v "$(pwd)/opt:/testzeus-hercules/opt" \
  --rm -it testzeus/hercules:latest

echo "Hercules run completed."

# (Optional) Cleanup: pkill -f "Google Chrome"