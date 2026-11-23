#!/usr/bin/env bash
# Check the built packages in dist/ before they are published (CI and the release workflow run this):
#   1. twine's strict metadata check (the README renders on PyPI, every required field is there);
#   2. the wheel carries what a run needs: the tester UI, the case schemas, the bundled axe-core;
#   3. the wheel installs into a fresh virtual environment and both commands start.
# Usage: scripts/check_dist.sh [DIST_DIR] (default dist). PYTHON picks the interpreter for step 3 (default 3.13).
set -euo pipefail

dist="${1:-dist}"
python="${PYTHON:-3.13}"
shopt -s nullglob
wheels=("$dist"/*.whl)
sdists=("$dist"/*.tar.gz)
if [ "${#wheels[@]}" -ne 1 ] || [ "${#sdists[@]}" -ne 1 ]; then
  echo "check_dist: expected one wheel and one sdist in $dist, found ${#wheels[@]} and ${#sdists[@]}" >&2
  exit 1
fi

echo "check_dist: twine check --strict"
uvx --from 'twine>=6.1,<7' twine check --strict "${wheels[0]}" "${sdists[0]}"

echo "check_dist: wheel contents"
uv run --no-project --python "$python" python - "${wheels[0]}" <<'EOF'
import sys
import zipfile

names = set(zipfile.ZipFile(sys.argv[1]).namelist())
needed = {
    "testzeus_hercules/ui/static/index.html": "the tester UI (run scripts/build_ui.sh before building)",
    "testzeus_hercules/py.typed": "the typing marker",
}
missing = [f"{path} ({why})" for path, why in needed.items() if path not in names]
if not any(name.startswith("testzeus_hercules/cases/schemas/") and name.endswith(".json") for name in names):
    missing.append("testzeus_hercules/cases/schemas/*.json (the case schemas)")
if not any(name.startswith("testzeus_hercules/quality/vendor/axe/") for name in names):
    missing.append("testzeus_hercules/quality/vendor/axe/ (axe-core and its licence)")
leaked = sorted(name for name in names if name.startswith(("tests/", "bench/", "ui/src/")) or name.endswith((".env", ".db")))
if missing or leaked:
    print("check_dist: missing: " + "; ".join(missing) if missing else "", file=sys.stderr)
    print("check_dist: should not ship: " + ", ".join(leaked[:10]) if leaked else "", file=sys.stderr)
    sys.exit(1)
print(f"check_dist: {len(names)} files in the wheel, UI and schemas present")
EOF

echo "check_dist: install into a fresh environment (Python $python)"
venv="$(mktemp -d)/venv"
uv venv --quiet --python "$python" "$venv"
uv pip install --quiet --python "$venv/bin/python" "${wheels[0]}"
"$venv/bin/hercules" version
"$venv/bin/hercules" --help > /dev/null
"$venv/bin/testzeus-hercules" --help > /dev/null
echo "check_dist: ok"
