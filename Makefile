SHELL := /bin/sh
PYTHON ?= python
COMPOSE ?= docker compose

.DEFAULT_GOAL := help

.PHONY: help up down build logs seed test test-unit test-integration lint format typecheck check eval web-install web-check

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full local stack (Docker)
	$(COMPOSE) up -d --build

down: ## Stop the local stack
	$(COMPOSE) down

build: ## Build container images
	$(COMPOSE) build

logs: ## Follow logs from all services
	$(COMPOSE) logs -f

seed: ## Load synthetic data (task 007 adds generators)
	$(PYTHON) -m travelos.admin.seed

test: ## Run all tests
	$(PYTHON) -m pytest

test-unit: ## Run unit tests only
	$(PYTHON) -m pytest tests/unit

test-integration: ## Run integration tests only
	$(PYTHON) -m pytest tests/integration -m integration

lint: ## Lint and format-check Python
	ruff check .
	ruff format --check .

format: ## Auto-format and auto-fix Python
	ruff format .
	ruff check --fix .

typecheck: ## Run mypy in strict mode
	mypy src

check: lint typecheck test ## The gate: lint + typecheck + tests

eval: ## Run LLM/quality evaluations (task 018)
	$(PYTHON) -m pytest tests/eval -m eval

web-install: ## Install dashboard dependencies
	cd web/dashboard && npm install

web-check: ## Typecheck and build the dashboard
	cd web/dashboard && npm run typecheck && npm run build
