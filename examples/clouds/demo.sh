#!/usr/bin/env bash
# Showcase: run one case with Hercules, then run its exported test on every connected cloud.
#
#   examples/clouds/demo.sh            # every connected cloud
#   examples/clouds/demo.sh testmu     # one cloud
#
# Needs: a configured model (hercules doctor), the cloud extra (pip install 'testzeus-hercules[cloud]'),
# and at least one connected cloud (Integrations screen, or hercules cloud connect).
# The demo app is the public Swag Labs site, which cloud browsers can reach.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
project="${HERCULES_DEMO_PROJECT:-$here/project}"
mkdir -p "$project"
cd "$project"

echo "--- 1. Run the case with Hercules"
result="$(hercules run "$here/swag-labs-sign-in.feature" --base-url https://www.saucedemo.com --json)" || {
  echo "$result"
  echo "the case did not pass; fix the model setup (hercules doctor) and try again" >&2
  exit 1
}
run_id="$(printf '%s' "$result" | python3 -c 'import json, sys; print(json.load(sys.stdin)["run_id"])')"
echo "passed: $run_id"

if [ "$#" -gt 0 ]; then
  clouds="$*"
else
  clouds="$(hercules cloud list --json | python3 -c 'import json, sys; print(" ".join(c["cloud"] for c in json.load(sys.stdin) if c["connected"]))')"
fi
if [ -z "$clouds" ]; then
  echo "no cloud is connected: open the tester UI's Integrations screen, or run hercules cloud connect" >&2
  exit 1
fi

status=0
for cloud in $clouds; do
  echo "--- 2. Run it on $cloud"
  hercules cloud run "$run_id" --cloud "$cloud" || status=1
  echo "--- 3. The project for $cloud CI"
  hercules cloud bundle "$run_id" --cloud "$cloud" --out "$project/swag-labs-$cloud.zip"
done
exit $status
