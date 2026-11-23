.ONESHELL:
# Tests of the repository itself (dependencies, release tooling), beside the v2 suite.
REPO_TESTS=tests/test_dependency_diet.py tests/test_backmerge_script.py
BLACK=uv run black --target-version py313 -l 200

.PHONY: help
help:             ## Show the help.
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@grep -F "##" Makefile | grep -Fv 'grep -F'


.PHONY: show
show:             ## Show the current environment.
	@uv --version
	@uv run python --version
	@uv run hercules version

.PHONY: install
install:          ## Install the project and its dev tools, and Playwright's Chromium.
	uv sync
	uv run python -m playwright install chromium

.PHONY: install-extra
install-extra:    ## Like install, with every optional extra (api).
	uv sync --all-extras
	uv run python -m playwright install chromium

.PHONY: fmt
fmt:              ## Format code with black.
	$(BLACK) testzeus_hercules/ tests/ bench/

.PHONY: lint
lint:             ## Check formatting with black (changes nothing).
	$(BLACK) --check testzeus_hercules/ tests/ bench/

.PHONY: test
test: lint        ## Run the offline tests (v2 suite + repository tests) with coverage.
	uv run playwright install --with-deps chromium
	uv run pytest -v -m "not live" --junit-xml=tests/test_output.xml --cov=testzeus_hercules -l --tb=short tests/v2 $(REPO_TESTS)
	uv run coverage xml
	uv run coverage html

.PHONY: watch
watch:            ## Run the offline tests on every change (needs entr).
	find testzeus_hercules tests -name '*.py' | entr uv run pytest -s -vvv -l --tb=long --maxfail=1 -m "not live" tests/v2

.PHONY: ui
ui:               ## Build the tester UI into testzeus_hercules/ui/static (needs Node.js 20+).
	bash scripts/build_ui.sh

.PHONY: clean
clean:            ## Clean unused files.
	@find ./ -name '*.pyc' -not -path './.venv/*' -exec rm -f {} \;
	@find ./ -name '__pycache__' -not -path './.venv/*' -prune -exec rm -rf {} \;
	@find ./ -name 'Thumbs.db' -exec rm -f {} \;
	@find ./ -name '*~' -not -path './.venv/*' -exec rm -f {} \;
	@rm -rf .cache
	@rm -rf .pytest_cache
	@rm -rf .mypy_cache
	@rm -rf build
	@rm -rf dist
	@rm -rf *.egg-info
	@rm -rf htmlcov
	@rm -f coverage.xml tests/test_output.xml

.PHONY: virtualenv
virtualenv:       ## Create the virtual environment (.venv) with every extra.
	uv sync --all-extras

.PHONY: release
release:          ## Set the version, commit and tag it: make release VERSION=2.1.0 (pushing the tag publishes).
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=2.1.0"; exit 2; }
	uv run python scripts/release.py $(VERSION)

.PHONY: build
build:            ## Build the sdist and wheel with the tester UI, and check them as the release does.
	bash scripts/build_ui.sh
	uv build
	bash scripts/check_dist.sh dist

.PHONY: publish
publish:          ## Not used for releases: the Release workflow publishes with PyPI trusted publishing.
	@echo "Releases are published by GitHub Actions when a version tag is pushed: make release VERSION=x.y.z"
	@exit 1

.PHONY: setup-uv
setup-uv:         ## Install uv.
	pip install uv
