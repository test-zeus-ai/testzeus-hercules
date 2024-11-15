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
	poetry install --all-extras && exit && poetry run playwright install --with-deps

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
	poetry run pytest -v --junit-xml=tests/test_output.xml --cov-config .coveragerc --cov=testzeus_hercules -l --tb=short --maxfail=1 tests/
	poetry run coverage xml
	poetry run coverage html

.PHONY: test-case  ## Run selective test case.
test-case: lint     ## Run a specific test case.
	@read -p "Enter the test case (e.g., multilingual): " TEST_CASE && \
	poetry run pytest -v --pdb tests/test_feature_execution.py::test_feature_execution[$$TEST_CASE]

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
	@echo "WARNING: This operation will create a version tag and push to GitHub"
	@read -p "Version bump (patch, minor, major)? : " BUMP  && \
	poetry version $$BUMP
	@VERSION=$(shell poetry version -s)  && \
	git add pyproject.toml  && \
	git commit -m "release: version $$VERSION 🚀"  && \
	echo "creating git tag : $$VERSION"  && \
	git tag $$VERSION
	@git push -u origin HEAD --tags
	@echo "Github Actions will detect the new tag and release the new version."

.PHONY: build
build:       ## build testzeus_hercules.
	poetry build

.PHONY: publish
publish:          ## Publish the package to PyPI.
	@echo "Publishing to PyPI ..."
	# @poetry config pypi-token.pypi $${TWINE_PASSWORD}
	# @poetry publish -n --verbose --build
	@pip install twine
	@twine upload dist/*

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
	@VERSION=$(shell poetry version -s) && \
	docker tag testzeus/hercules testzeus/hercules:$${VERSION}

.PHONY: docker-publish
docker-publish:          ## Publish the package to Docker registry.
	@VERSION=$(shell poetry version -s) && \
	docker push testzeus/hercules:$${VERSION}
	docker push testzeus/hercules:latest