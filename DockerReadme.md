export HF_HOME=.cache
docker build -t testzeus-hercules-alpha-0 .
docker run -e HF_HOME='/testzeus-hercules/.cache' --rm -it --entrypoint bash testzeus-hercules-alpha-0

docker run --env-file=.env -v ./.cache:/testzeus-hercules/.cache -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json -v ./opt:/testzeus-hercules/opt --rm -it testzeus-hercules-alpha-0
docker run --env-file=.env -v ./.cache:/testzeus-hercules/.cache -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json -v ./opt:/testzeus-hercules/opt --rm -it --entrypoint bash testzeus-hercules-alpha-0


docker run --env-file=.env -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json -v ./opt:/testzeus-hercules/opt --rm -it testzeus-hercules-alpha-0