#!/bin/bash
# set -ex

# curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Create a new Python virtual environment named 'test'
python3.11 -m venv test

# Activate the virtual environment
source test/bin/activate

# Upgrade the 'testzeus-hercules' package
pip install --upgrade testzeus-hercules
playwright install --with-deps

# create a new directory named 'opt'
mkdir -p opt/input opt/output opt/test_data

# download https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/refs/heads/main/agents_llm_config-example.json
curl -sS https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/main/agents_llm_config-example.json > agents_llm_config-example.json
mv agents_llm_config-example.json agents_llm_config.json

# prompt user that they need to edit the agents_llm_config.json file, halt the script and open the file in an editor
echo "Please edit the 'agents_llm_config.json' file to add your API key and other configurations."

# halt the script and mention the absolute path of the agents_llm_config.json file so that user can edit it in the editor
echo "The 'agents_llm_config.json' file is located at $(pwd)/agents_llm_config.json"
read -p "Press Enter if file is updated"

# download https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/blob/main/.env-example
curl -sS https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/main/.env-example > .env-example
mv .env-example .env

# prompt user that they need to edit the .env file, halt the script and open the file in an editor
echo "The '.env' file is located at $(pwd)/.env"
read -p "Press Enter if file is updated"

# create a input/test.feature file
# download https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/refs/heads/main/opt/input/test.feature and save in opt/input/test.feature
curl -sS https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/main/opt/input/test.feature > opt/input/test.feature

# download https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/refs/heads/main/opt/test_data/test_data.json and save in opt/test_data/test_data.json
curl -sS https://raw.githubusercontent.com/test-zeus-ai/testzeus-hercules/main/opt/test_data/test_data.json > opt/test_data/test_data.json


# Run the 'testzeus-hercules' command with the specified parameters
testzeus-hercules --project-base=opt