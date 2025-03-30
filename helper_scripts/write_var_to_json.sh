#!/bin/bash
set -e

# Usage: ./write_var_to_json.sh x1.json

OUTPUT_FILE="$1"

if [[ -z "$AGENTS_LLM_CONFIG_JSON" ]]; then
  echo "AGENTS_LLM_CONFIG_JSON is not set. Please run the other script or source agents_env.sh."
  exit 1
fi

echo "$AGENTS_LLM_CONFIG_JSON" > "$OUTPUT_FILE"

echo "Wrote AGENTS_LLM_CONFIG_JSON to $OUTPUT_FILE"