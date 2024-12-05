#!/bin/bash

# curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Create a new Python virtual environment named 'test'
python3 -m venv test

# Activate the virtual environment
source test/bin/activate

# Upgrade the 'testzeus-hercules' package
pip install --upgrade testzeus-hercules
playwright install --with-deps

#Set Headless as false
export HEADLESS=false

# create a new directory named 'opt'
mkdir -p opt/input opt/output opt/test_data

# create a intput/test.feature file
echo "Feature: Open Google homepage\nScenario: User opens Google homepage\n  Given I have a web browser open\n  When I navigate to https://www.google.com\n  Then I should see the Google homepage" > opt/input/test.feature

# get gpt-4o model API key by asking user
echo "Enter your GPT-4o model API key:"
read -s GPT_4O_API_KEY

# Run the 'testzeus-hercules' command with the specified parameters
testzeus-hercules \
  --input-file opt/input/test.feature \
  --output-path opt/output \
  --test-data-path opt/test_data \
  --llm-model gpt-4o \
  --llm-model-api-key $GPT_4O_API_KEY
