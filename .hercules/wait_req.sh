#!/bin/bash
# Print the oldest unanswered mock-LLM request, or WAIT_TIMEOUT after $1 seconds.
DIR="$(cd "$(dirname "$0")" && pwd)/mock_llm"
TIMEOUT=${1:-240}
t0=$(date +%s)
while :; do
  for f in $(ls "$DIR"/req/*.json 2>/dev/null | sort); do
    id=$(basename "$f" .json)
    if [ ! -f "$DIR/resp/$id.json" ]; then
      echo "REQ_ID=$id"
      cat "$f"
      exit 0
    fi
  done
  now=$(date +%s)
  [ $((now - t0)) -ge "$TIMEOUT" ] && { echo "WAIT_TIMEOUT"; exit 2; }
  sleep 1
done
