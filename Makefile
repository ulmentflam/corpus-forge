.PHONY: help install dev lint format typecheck test test-unit test-integration test-fuzz test-smoke \
        migrate ingest embed backfill daemon stop logs ci docs docs-serve clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?##"};{printf "  %-18s %s\n",$$1,$$2}'

install: ## Install runtime dependencies (uv sync)
	uv sync

dev: ## Install dev dependencies + pre-commit hook
	uv sync --all-extras --group dev
	uv run pre-commit install

lint: ## ruff check
	uv run ruff check corpus_forge tests

format: ## ruff format (writes)
	uv run ruff format corpus_forge tests

format-check: ## ruff format --check (CI)
	uv run ruff format --check corpus_forge tests

typecheck: ## pyrefly strict
	uv run pyrefly check corpus_forge

test: test-unit test-integration test-fuzz test-smoke ## All four test categories

test-unit: ## Fast, parallel, no Docker, coverage-gated
	uv run pytest tests/unit -v -n auto --timeout=60 --cov=corpus_forge --cov-report=term-missing --cov-fail-under=85

test-integration: ## Requires Docker (testcontainers pgvector)
	uv run pytest tests/integration -v

test-fuzz: ## Hypothesis-driven property tests (HYPOTHESIS_PROFILE=dev|ci|nightly)
	HYPOTHESIS_PROFILE=$${HYPOTHESIS_PROFILE:-dev} uv run pytest tests/fuzz -v --hypothesis-show-statistics

test-smoke: ## End-to-end happy paths against fake embedder
	uv run pytest tests/smoke -v

migrate: ## Apply schema migrations
	uv run corpus-forge migrate

ingest: ## One-shot ingestion pass
	uv run corpus-forge ingest --once

embed: ## Backfill an embedder: make embed E=qwen3_8b
	uv run corpus-forge embed --embedder=$(E)

backfill: ## Backfill all active embedders
	uv run corpus-forge embed

daemon: ## Run daemon in foreground (dev)
	uv run corpus-forge daemon

stop: ## Stop launchd-managed daemon
	./scripts/stop.sh

logs: ## Tail launchd error log
	tail -f ~/Library/Logs/corpus-forge.err.log

ci: format-check lint typecheck test ## Full CI pipeline (run before pushing)

docs-serve: ## Live-preview docs locally (mkdocs)
	uv run mkdocs serve

docs: ## Build docs to ./site
	uv run mkdocs build

clean:
	rm -rf .venv .pytest_cache .ruff_cache .coverage htmlcov dist build site
	find . -type d -name __pycache__ -exec rm -rf {} +