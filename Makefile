.ONESHELL:
ENV_PREFIX=$(shell python -c "if __import__('pathlib').Path('.venv/bin/pip').exists(): print('.venv/bin/')")
USING_UV=$(shell grep "uv" pyproject.toml && echo "yes")

.PHONY: help
help:             ## Show the help.
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@fgrep "##" Makefile | fgrep -v fgrep


.PHONY: show
show:             ## Show the current environment.
	@echo "Current environment:"
	uv python info && exit

.PHONY: install-extra
install-extra:          ## Install the project in dev mode.
	uv sync --all-extras && exit && uv run playwright install --with-deps

.PHONY: install
install:          ## Install the project in dev mode.
	uv sync && exit && uv run playwright install --with-deps

.PHONY: fmt
fmt:              ## Format code using black & isort.
	uv run isort testzeus_hercules/
	uv run black -l 200 testzeus_hercules/
	uv run black -l 200 tests/

.PHONY: lint
lint: fmt             ## Run pep8, black, mypy linters.
	uv run black -l 200 --check testzeus_hercules/
	uv run black -l 200 --check tests/
	# uv run mypy --ignore-missing-imports testzeus_hercules/

.PHONY: test
test: lint        ## Run tests and generate coverage report.
	uv run playwright install --with-deps
	uv run pytest -v --junit-xml=tests/test_output.xml --cov-config .coveragerc --cov=testzeus_hercules -l --tb=short --maxfail=1 tests/
	uv run coverage xml
	uv run coverage html

.PHONY: test-case  ## Run selective test case.
test-case: lint     ## Run a specific test case.
	@read -p "Enter the test case (e.g., productSearch): " TEST_CASE && \
	uv run pytest -v tests/test_feature_execution.py::test_feature_execution[$$TEST_CASE]

.PHONY: watch
watch:            ## Run tests on every change.
	ls **/**.py | entr uv run pytest -s -vvv -l --tb=long --maxfail=1 tests/

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

.PHONY: cleanup
cleanup:            ## Delete output and log folders.
	@rm -rf opt/output opt/log_files opt/proofs opt/gherkin_files
	@echo "Cleaned up output folders"

.PHONY: virtualenv
virtualenv:       ## Create a virtual environment.
	uv sync --all-extras && exit

.PHONY: release
release: ## Create a new tag for release.
	@echo "WARNING: This operation will create a version tag and push to GitHub"
	@( \
	  read -p "Version bump (patch, minor, major)? : " BUMP; \
	  python -c "import re, sys; \
	    content = open('pyproject.toml').read(); \
	    version_match = re.search(r'version = \"([^\"]+)\"', content); \
	    if not version_match: sys.exit(1); \
	    current = version_match.group(1).split('.'); \
	    if sys.argv[1] == 'patch': current[2] = str(int(current[2])+1); \
	    elif sys.argv[1] == 'minor': current[1] = str(int(current[1])+1); current[2] = '0'; \
	    elif sys.argv[1] == 'major': current[0] = str(int(current[0])+1); current[1] = current[2] = '0'; \
	    new_version = '.'.join(current); \
	    new_content = re.sub(r'version = \"[^\"]+\"', f'version = \"{new_version}\"', content); \
	    open('pyproject.toml', 'w').write(new_content); \
	    print(new_version)" $$BUMP > .tmp_version; \
	  read -p "Do you want to continue? (y/n) : " CONTINUE; \
	  [ $$CONTINUE = "y" ] || exit 1; \
	  VERSION=$$(cat .tmp_version); \
	  echo "New Version: $$VERSION"; \
	  git add pyproject.toml; \
	  git commit -m "release: version $$VERSION 🚀"; \
	  echo "creating git tag : $$VERSION"; \
	  git tag $$VERSION; \
	  git push -u origin HEAD --tags; \
	  rm .tmp_version; \
	  echo "GitHub Actions will detect the new tag and release the new version."; \
	)

.PHONY: build
build:       ## build testzeus_hercules.
	uv build

.PHONY: publish
publish:          ## Publish the package to PyPI.
	@echo "Publishing to PyPI ..."
	# @poetry config pypi-token.pypi $${TWINE_PASSWORD}
	# @poetry publish -n --verbose --build
	@pip install twine
	@twine upload dist/*

.PHONY: run
run:       ## run testzeus_hercules.
	uv run python testzeus_hercules


.PHONY: run-interactive
run-interactive:       ## run-interactive testzeus_hercules.
	uv run python -m testzeus_hercules.interactive

.PHONY: setup-uv
setup-uv:       ## setup uv.
	pip install uv

.PHONY: docker-build
docker-build:       ## build and tag docker image.
	docker build -t testzeus/hercules .
	@VERSION=$$(grep -E '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/') && \
	docker tag testzeus/hercules testzeus/hercules:$${VERSION}

.PHONY: docker-publish
docker-publish:          ## Publish the package to Docker registry.
	@VERSION=$$(grep -E '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/') && \
	docker push testzeus/hercules:$${VERSION}
	docker push testzeus/hercules:latest