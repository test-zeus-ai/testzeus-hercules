#!/bin/bash

# Usage: ./write_json_to_var.sh x.json

INPUT_FILE="$1"

if [[ ! -f "$INPUT_FILE" ]]; then
  echo "Input file '$INPUT_FILE' not found!"
  exit 1
fi

# Read file content into AGENTS_LLM_CONFIG_JSON
export AGENTS_LLM_CONFIG_JSON="$(<"$INPUT_FILE")"

# Optional: save to a file for sourcing in another script
echo "export AGENTS_LLM_CONFIG_JSON='$(<"$INPUT_FILE" | sed "s/'/'\\\\''/g")'" > agents_env.sh

echo "AGENTS_LLM_CONFIG_JSON exported."
echo "AGENTS_LLM_CONFIG_JSON: $AGENTS_LLM_CONFIG_JSON"