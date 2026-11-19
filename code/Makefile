PYTHON ?= .venv/bin/python
PYTHON_BOOTSTRAP ?= python3.11
PYTHON_SOURCES = nexus_research tests scripts

.PHONY: help setup dev run app installer verify-app test lint format check

help:
	@echo 'make dev         Install runtime and development tools'
	@echo 'make run         Start Research on http://127.0.0.1:8774'
	@echo 'make app         Build dist/Nexus Research.app'
	@echo 'make installer   Package ../Install Nexus.dmg'
	@echo 'make verify-app  Verify the packaged backend with synthetic data'
	@echo 'make check       Run lint, formatting, syntax and tests'

setup:
	@test -x $(PYTHON) || $(PYTHON_BOOTSTRAP) -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt

dev: setup
	$(PYTHON) -m pip install -r requirements-dev.txt
	npm ci --ignore-scripts --no-audit --no-fund --cache .local/npm-cache

run:
	$(PYTHON) -m nexus_research

app:
	$(PYTHON) -m pip install -r requirements-build.txt
	$(PYTHON) -m scripts.build_app

verify-app:
	$(PYTHON) -m scripts.verify_app

installer:
	$(PYTHON) -m scripts.build_installer

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(PYTHON) -m ruff check $(PYTHON_SOURCES)
	$(PYTHON) -m ruff format --check $(PYTHON_SOURCES)
	npm run format:check
	npm run check:js

format:
	$(PYTHON) -m ruff format $(PYTHON_SOURCES)
	npm run format

check: lint test
