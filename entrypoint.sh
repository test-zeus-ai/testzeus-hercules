#!/bin/sh

# Check that either LLM_MODEL_NAME and LLM_MODEL_API_KEY are set together,
# or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY are set together
if { [ -n "$LLM_MODEL_NAME" ] && [ -n "$LLM_MODEL_API_KEY" ]; } && \
   { [ -n "$AGENTS_LLM_CONFIG_FILE" ] || [ -n "$AGENTS_LLM_CONFIG_FILE_REF_KEY" ]; }; then
    echo "Error: Provide either LLM_MODEL_NAME and LLM_MODEL_API_KEY together, or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY together, not both."
    exit 1
fi

if { [ -z "$LLM_MODEL_NAME" ] || [ -z "$LLM_MODEL_API_KEY" ]; } && \
   { [ -z "$AGENTS_LLM_CONFIG_FILE" ] || [ -z "$AGENTS_LLM_CONFIG_FILE_REF_KEY" ]; }; then
    echo "Error: Either LLM_MODEL_NAME and LLM_MODEL_API_KEY must be set together, or AGENTS_LLM_CONFIG_FILE and AGENTS_LLM_CONFIG_FILE_REF_KEY must be set together."
    exit 1
fi

# Check for the /testzeus-hercules/opt directory and set up test case if it doesn't exist
if [ ! -d "/testzeus-hercules/opt" ]; then
  echo "Warning: /testzeus-hercules/opt directory is not found. This means the results will be displayed in logs, and a sample test case will be executed."
  mkdir -p /testzeus-hercules/opt/input /testzeus-hercules/opt/test_data
  echo "Feature: Open Google homepage\nScenario: User opens Google homepage\n  Given I have a web browser open\n  When I navigate to https://www.google.com\n  Then I should see the Google homepage" > /testzeus-hercules/opt/input/test.feature
  touch /testzeus-hercules/opt/test_data/td.txt
fi

# Execute the main application
exec poetry run python testzeus_hercules/main.py
