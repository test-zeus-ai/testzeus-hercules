export HF_HOME=.cache
docker build -t hercules-alpha-0 .
docker run -e HF_HOME='/hercules/.cache' --rm -it --entrypoint bash hercules-alpha-0

docker run --env-file=.env -v ./.cache:/hercules/.cache -v ./agents_llm_config.json:/hercules/agents_llm_config.json -v ./opt:/hercules/opt --rm -it hercules-alpha-0
docker run --env-file=.env -v ./.cache:/hercules/.cache -v ./agents_llm_config.json:/hercules/agents_llm_config.json -v ./opt:/hercules/opt --rm -it --entrypoint bash hercules-alpha-0


docker run --env-file=.env -v ./agents_llm_config.json:/hercules/agents_llm_config.json -v ./opt:/hercules/opt --rm -it hercules-alpha-0