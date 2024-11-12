.ONESHELL:
ENV_PREFIX=$(shell python -c "if __import__('pathlib').Path('.venv/bin/pip').exists(): print('.venv/bin/')")
USING_POETRY=$(shell grep "tool.poetry" pyproject.toml && echo "yes")

.PHONY: help
help:             ## Show the help.
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@fgrep "##" Makefile | fgrep -v fgrep


.PHONY: show
show:             ## Show the current environment.
	@echo "Current environment:"
	poetry env info && exit

.PHONY: install
install:          ## Install the project in dev mode.
	poetry install --all-extras && exit

.PHONY: fmt
fmt:              ## Format code using black & isort.
	poetry run isort testzeus_hercules/
	poetry run black -l 200 testzeus_hercules/
	poetry run black -l 200 tests/

.PHONY: lint
lint: fmt             ## Run pep8, black, mypy linters.
	poetry run black -l 200 --check testzeus_hercules/
	poetry run black -l 200 --check tests/
	# poetry run mypy --ignore-missing-imports testzeus_hercules/

.PHONY: test
test: lint        ## Run tests and generate coverage report.
	poetry run playwright install --with-deps
	poetry run pytest -v --junit-xml=test_output.xml --cov-config .coveragerc --cov=testzeus_hercules -l --tb=short --maxfail=1 tests/
	poetry run coverage xml
	poetry run coverage html

.PHONY: watch
watch:            ## Run tests on every change.
	ls **/**.py | entr poetry run pytest -s -vvv -l --tb=long --maxfail=1 tests/

.PHONY: clean
clean:            ## Clean unused files.
	@find ./ -name '*.pyc' -exec rm -f {} \;
	@find ./ -name '__pycache__' -exec rm -rf {} \;
	@find ./ -name 'Thumbs.db' -exec rm -f {} \;
	@find ./ -name '*~' -exec rm -f {} \;
	@rm -rf .cache
	@rm -rf .pytest_cache
	@rm -rf .mypy_cache
	@rm -rf build
	@rm -rf dist
	@rm -rf *.egg-info
	@rm -rf htmlcov
	@rm -rf .tox/
	@rm -rf docs/_build

.PHONY: virtualenv
virtualenv:       ## Create a virtual environment.
	poetry install --all-extras && exit

.PHONY: release
release:          ## Create a new tag for release.
	@echo "WARNING: This operation will create s version tag and push to github"
	@read -p "Version? (provide the next x.y.z semver) : " TAG
	@echo "$${TAG}" > testzeus_hercules/VERSION
	@$(ENV_PREFIX)gitchangelog > HISTORY.md
	@git add testzeus_hercules/VERSION HISTORY.md
	@git commit -m "release: version $${TAG} 🚀"
	@echo "creating git tag : $${TAG}"
	@git tag $${TAG}
	@git push -u origin HEAD --tags
	@echo "Github Actions will detect the new tag and release the new version."

.PHONY: docs
docs:             ## Build the documentation.
	@echo "building documentation ..."
	@poetry run mkdocs build
	URL="site/index.html"; xdg-open $$URL || sensible-browser $$URL || x-www-browser $$URL || gnome-open $$URL || open $$URL


.PHONY: build
build:       ## build testzeus_hercules.
	poetry build


.PHONY: run
run:       ## run testzeus_hercules.
	poetry run python testzeus_hercules


.PHONY: run-interactive
run-interactive:       ## run-interactive testzeus_hercules.
	poetry run python -m testzeus_hercules.interactive

.PHONY: setup-poetry
setup-poetry:       ## setup poetry.
	curl -sSL https://install.python-poetry.org | python3.11 -

.PHONY: docker-build
docker-build:       ## build and tag docker image.
	docker build -t testzeus/hercules .
	@VERSION=$(shell cat testzeus_hercules/VERSION) && \
	docker tag testzeus/hercules testzeus/hercules:$${VERSION}

.PHONY: publish
publish:          ## Publish the package to PyPI and Docker registry.
	# poetry publish --build
	@VERSION=$(shell cat testzeus_hercules/VERSION) && \
	docker push testzeus/hercules:$${VERSION}
	docker push testzeus/hercules:latest