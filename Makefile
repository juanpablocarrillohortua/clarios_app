# =============================================================================
# Clarios Energy - developer entry point
#
# Every developer and CI action goes through this file. `make check` is THE
# quality gate: if it is green locally, CI will be green too.
#
# Windows note: GNU Make defaults to cmd.exe, which cannot run these recipes, so
# Git Bash is selected below. It is located through `git --exec-path` rather than
# by name, which means `make` behaves identically from PowerShell, cmd and Git
# Bash, and no PATH changes are needed.
# =============================================================================

ifeq ($(OS),Windows_NT)
# `bash.exe` alone is ambiguous here: resolved through the Windows PATH it can
# find C:\Windows\System32\bash.exe, the WSL launcher, which carries docker but
# not uv - so `make up` would succeed while every quality gate died with
# "uv: command not found". Derive Git Bash from git's own install location so
# the same shell is used from PowerShell, cmd and Git Bash alike.
GIT_BASH := $(abspath $(shell git --exec-path)/../../../bin/bash.exe)
SHELL := $(if $(wildcard $(GIT_BASH)),$(GIT_BASH),bash.exe)
# MSYS rewrites values that look like POSIX paths when it hands them to a native
# Windows binary, so an exported DASH_URL_BASE_PATHNAME=/ reaches docker as
# `D:/git/Git/` and Dash rejects it. These switches turn that rewriting off.
export MSYS_NO_PATHCONV := 1
export MSYS2_ARG_CONV_EXCL := *
export MSYS2_ENV_CONV_EXCL := *
else
SHELL := /bin/bash
endif
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# Load .env so database credentials and ports are available to recipes.
ifneq (,$(wildcard ./.env))
include .env
export
endif

UV := uv
DC := docker compose
PY := $(UV) run

# Alembic needs a reachable database. The compose stack deliberately publishes
# no database port (only Caddy publishes ports), so migrations run *inside* the
# api container. In CI the database is a service container on localhost, so
# Alembic runs directly on the host. Override with `make db-upgrade RUNNER=host`.
RUNNER ?= $(if $(filter true,$(CI)),host,compose)
ifeq ($(RUNNER),host)
ALEMBIC := $(PY) alembic -c pyproject.toml
else
ALEMBIC := $(DC) run --rm --no-deps api alembic -c pyproject.toml
endif

.PHONY: help install up down logs restart format format-check lint typecheck \
        security test test-unit test-integration check db-upgrade db-downgrade \
        db-revision db-reset seed equs-load clean

# -----------------------------------------------------------------------------
# Meta
# -----------------------------------------------------------------------------
help: ## Show all available targets
	@echo ""
	@echo "Clarios Energy - available targets:"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------
install: ## Install dependencies with uv and set up pre-commit hooks
	$(UV) python install 3.12
	$(UV) sync --all-groups
	$(PY) pre-commit install --install-hooks
	@echo "==> Environment ready. Next: cp .env.example .env && make up"

# -----------------------------------------------------------------------------
# Stack lifecycle
# -----------------------------------------------------------------------------
up: ## Start the full stack with docker compose
	$(DC) up -d --build --wait
	@$(DC) ps

down: ## Stop the stack
	$(DC) down --remove-orphans

logs: ## Tail logs from all services
	$(DC) logs -f --tail=100

restart: ## Restart the stack
	$(MAKE) down
	$(MAKE) up

# -----------------------------------------------------------------------------
# Quality gates
# -----------------------------------------------------------------------------
format: ## Auto-format code with ruff
	$(PY) ruff format .
	$(PY) ruff check --fix-only .

format-check: ## Verify formatting without modifying files
	$(PY) ruff format --check .

lint: ## Run ruff check
	$(PY) ruff check .

typecheck: ## Run mypy in strict mode
	$(PY) mypy

security: ## Run bandit and pip-audit
	$(PY) bandit -c pyproject.toml -r src scripts -q
	@mkdir -p reports
	# Audit the exported lockfile rather than the live environment: --no-emit-project
	# excludes this package itself, which is not on PyPI and would otherwise abort
	# the run under --strict.
	$(UV) export --format requirements-txt --no-emit-project --no-hashes --all-groups \
		-o reports/requirements.txt --quiet
	$(PY) pip-audit --strict --progress-spinner off -r reports/requirements.txt

test: ## Run the full test suite with coverage
	$(PY) pytest --cov --cov-report=term-missing --cov-report=xml --cov-report=html

test-unit: ## Run unit tests only (fast, no containers)
	$(PY) pytest -m "not integration and not e2e" --no-cov -q

test-integration: ## Run integration tests (requires database)
	$(PY) pytest -m integration --no-cov

check: format-check lint typecheck security test ## Run the complete quality gate (this is what CI runs)
	@echo "==> All quality gates passed."

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
db-upgrade: ## Apply Alembic migrations to head
	$(ALEMBIC) upgrade head

db-downgrade: ## Roll back one migration
	$(ALEMBIC) downgrade -1

db-revision: ## Create a new autogenerated migration (msg="...")
	@test -n "$(msg)" || { echo "Usage: make db-revision msg=\"describe the change\""; exit 1; }
	$(ALEMBIC) revision --autogenerate -m "$(msg)"

db-reset: ## Drop, recreate, and re-migrate the database
	$(DC) exec -T db psql -U "$(POSTGRES_USER)" -d "$(POSTGRES_DB)" -v ON_ERROR_STOP=1 \
		-c 'DROP SCHEMA IF EXISTS gold, silver, bronze, reference CASCADE; DROP TABLE IF EXISTS public.alembic_version;'
	$(MAKE) db-upgrade

seed: ## Load reference data and synthetic sample data
	$(DC) run --rm --no-deps api python scripts/seed_reference_data.py
	$(DC) run --rm --no-deps api python scripts/generate_synthetic_data.py

# The workbooks are not part of the image, so their directory is mounted for the
# run - read-only, because the loader must never modify a source file. Override
# with `make equs-load EQUS_DIR=/some/other/dir`.
EQUS_DIR ?= $(CURDIR)/data/raw_excels

equs-load: ## Land the EQUS workbooks of data/raw_excels in Bronze
	$(DC) run --rm --no-deps -v "$(EQUS_DIR):/app/data/raw_excels:ro" api \
		energy equs load-all --dir /app/data/raw_excels

# -----------------------------------------------------------------------------
# Housekeeping
# -----------------------------------------------------------------------------
clean: ## Remove caches, build artifacts, and coverage reports
	rm -rf reports .coverage .coverage.* coverage.xml htmlcov dist build .eggs
	rm -rf .pytest_cache .ruff_cache .mypy_cache .hypothesis
	find . -type d -name '__pycache__' -not -path './.venv/*' -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.py[co]' -not -path './.venv/*' -delete 2>/dev/null || true
	@echo "==> Workspace cleaned."
