#!/usr/bin/env bash
# Build the tester UI into testzeus_hercules/ui/static (served by `hercules serve`).
# Run before building the wheel; needs Node.js 20+ and npm. Installs exactly the
# versions in ui/package-lock.json.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/ui"

if ! command -v npm >/dev/null 2>&1; then
  echo "build_ui: npm is not installed (install Node.js 20 or newer)" >&2
  exit 1
fi

npm ci --no-audit --no-fund
npm test -- --run
npm run build

test -f "$root/testzeus_hercules/ui/static/index.html" || { echo "build_ui: index.html was not produced" >&2; exit 1; }
echo "build_ui: UI built into testzeus_hercules/ui/static"
