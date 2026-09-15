# ACKSTREET AGENT — development tasks.
#
#   make help          list every target
#   make install       set up a local virtualenv
#   make test          run the test suite
#   make e2e           prove the loop against the bundled mock provider

VENV       ?= .venv
PY         := $(VENV)/bin/python
PIP        := $(VENV)/bin/pip
ACKSTREET  := $(VENV)/bin/ackstreet
E2E_HOME   ?= /tmp/ackstreet-e2e
MOCK_PORT  ?= 8099

.DEFAULT_GOAL := help

.PHONY: help venv install install-dev test test-cov lint fmt typecheck e2e e2e-connectors mock clean build docker-build docker-run dist doctor skills

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip setuptools wheel

install: venv ## Install the package (runtime deps only)
	@$(PIP) install --quiet -e .
	@echo "installed. try: $(ACKSTREET) doctor"

install-dev: venv ## Install with development extras
	@$(PIP) install --quiet -e ".[dev,yaml]"
	@echo "dev environment ready."

test: ## Run the test suite
	@$(PY) -m pytest -q

test-cov: ## Run tests with a coverage report
	@$(PY) -m pytest --cov=ackstreet --cov-report=term-missing -q

lint: ## Lint with ruff
	@$(PY) -m ruff check ackstreet tests scripts

fmt: ## Auto-fix lint issues
	@$(PY) -m ruff check --fix ackstreet tests scripts

typecheck: ## Type-check if mypy is installed
	@$(PY) -m mypy ackstreet || echo "mypy not installed (pip install mypy)"

doctor: ## Health-check the local install
	@$(ACKSTREET) doctor

skills: ## List skills the agent has learned
	@$(ACKSTREET) skills list

mock: ## Start the mock OpenAI-compatible server (foreground)
	@$(PY) scripts/mock_openai_server.py --port $(MOCK_PORT)

e2e: ## Run the end-to-end proof against the mock provider
	@bash scripts/e2e_demo.sh

e2e-connectors: ## Prove the chat-connector loop (fake Bot API + mock model)
	@bash scripts/connector_e2e.sh

build: ## Build sdist and wheel into dist/
	@$(PIP) install --quiet build
	@$(PY) -m build
	@ls -lh dist/

docker-build: ## Build the container image
	docker build -f docker/Dockerfile -t ackstreet-agent:latest .

docker-run: ## Run the container (opens chat)
	docker run -it --rm -e OPENAI_API_KEY=$$OPENAI_API_KEY \
		-v ackstreet-home:/home/ackstreet/.ackstreet \
		ackstreet-agent:latest chat

clean: ## Remove caches and build artefacts
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info htmlcov .coverage
	@echo "cleaned."
