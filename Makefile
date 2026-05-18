.PHONY: help install dev lint format typecheck test test-unit test-integration test-fuzz test-smoke \
        test-ocr test-ocr-local migrate ingest embed backfill daemon stop logs ci docs docs-serve clean

# Cross-platform dispatch: `make stop` / `make logs` need to talk to launchd
# on macOS and to systemd-user on Linux. Detect OS once at the top.
OS := $(shell uname -s)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?##"};{printf "  %-18s %s\n",$$1,$$2}'

install: ## Install runtime dependencies (uv sync)
	uv sync
	@$(MAKE) --no-print-directory _unhide-pth

dev: ## Install dev dependencies + pre-commit hook
	uv sync --all-extras --group dev
	uv run pre-commit install
	@$(MAKE) --no-print-directory _unhide-pth

# Darwin-only workaround: when the repo lives in iCloud Drive (~/Library/Mobile
# Documents/...), every freshly written file inherits the parent UF_HIDDEN flag,
# which makes site.py silently skip every .pth file — breaking our editable
# install (and therefore `import corpus_forge` from tests). Strip the flag
# after each sync so the .pth gets honoured. No-op on non-darwin systems.
.PHONY: _unhide-pth _warmup-grammars
_unhide-pth:
ifeq ($(OS),Darwin)
	@if [ -d .venv/lib ]; then \
	  find .venv/lib -name "*.pth" -exec chflags nohidden {} + 2>/dev/null || true; \
	fi
endif

lint: ## ruff check
	uv run ruff check corpus_forge tests

format: ## ruff format (writes)
	uv run ruff format corpus_forge tests

format-check: ## ruff format --check (CI)
	uv run ruff format --check corpus_forge tests

typecheck: ## pyrefly strict
	uv run pyrefly check corpus_forge

test: test-unit test-integration test-fuzz test-smoke ## All four test categories

test-unit: _warmup-grammars ## Fast, parallel, no Docker, coverage-gated
	uv run pytest tests/unit tests/ui tests/cli tests/admin tests/diagnostics tests/embedders tests/backends -v -n auto --timeout=60 --cov=corpus_forge --cov-report=term-missing --cov-fail-under=90

# Pre-warm the tree-sitter grammar cache in a single process so the
# parallel pytest-xdist workers don't race on `pack.download()` writes.
# Idempotent — fast no-op once the cache is populated. The set of
# languages mirrors what the unit suite exercises in code chunker /
# code extractor tests; expand as new languages are added to the corpus.
_warmup-grammars:
	@uv run python -c "import tree_sitter_language_pack as pack; \
pack.download(['python', 'javascript', 'typescript', 'tsx', 'go', 'rust', \
'java', 'kotlin', 'scala', 'ruby', 'elixir', 'erlang', 'haskell', 'ocaml', \
'clojure', 'commonlisp', 'scheme', 'bash', 'sql', 'css', 'lua', 'zig', \
'nim', 'crystal', 'r', 'julia', 'swift', 'dart', 'nix', 'c', 'cpp', \
'make', 'dockerfile'])" 2>&1 | tail -3 || true

test-integration: ## Requires Docker (testcontainers pgvector)
	uv run pytest tests/integration -v

test-fuzz: ## Hypothesis-driven property tests (HYPOTHESIS_PROFILE=dev|ci|nightly)
	HYPOTHESIS_PROFILE=$${HYPOTHESIS_PROFILE:-dev} uv run pytest tests/fuzz -v --hypothesis-show-statistics

test-smoke: ## End-to-end happy paths against fake embedder
	uv run pytest tests/smoke -v

# Phase D / Wave 6 (P1 gate): the live-OCR end-to-end tests are
# marker-gated. They are *collected* as part of the regular integration
# suite but auto-skip via tests/integration/conftest.py when their
# dependency is absent (Ollama daemon down, MISTRAL_API_KEY unset). The
# targets below force them to run by selecting the markers directly.
test-ocr: ## Run both live OCR suites (requires_ollama or requires_mistral_api)
	uv run pytest tests/integration -v -m "requires_ollama or requires_mistral_api"

test-ocr-local: ## Run the live-Ollama OCR suite only (requires_ollama)
	uv run pytest tests/integration -v -m requires_ollama

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

stop: ## Stop the corpus-forge daemon (macOS launchd / Linux systemd-user)
ifeq ($(OS),Darwin)
	./scripts/macos/stop.sh
else ifeq ($(OS),Linux)
	systemctl --user stop corpus-forge.service || true
else
	@echo "make stop: unsupported OS '$(OS)' — run 'corpus-forge daemon' supervisor manually."
endif

logs: ## Tail the corpus-forge daemon logs (macOS launchd / Linux journald)
ifeq ($(OS),Darwin)
	tail -f ~/Library/Logs/corpus-forge.err.log
else ifeq ($(OS),Linux)
	journalctl --user -u corpus-forge.service -f
else
	@echo "make logs: unsupported OS '$(OS)' — check your supervisor's log destination."
endif

ci: format-check lint typecheck test ## Full CI pipeline (run before pushing)

# Replicate the GitHub Actions ``ci.yml`` gate locally — same env (FORCE_COLOR
# triggers Rich/typer's ANSI help wrapping that broke CliRunner asserts),
# same step order, and bypasses ``uv run`` for pytest so the iCloud-Drive
# .pth hidden-flag fix isn't undone between steps.
ci-local: ## CI gate locally (FORCE_COLOR=1, HYPOTHESIS_PROFILE=ci) — catches CI-only failures
ifeq ($(OS),Darwin)
	@find .venv -name "*.pth" -exec chflags nohidden {} + 2>/dev/null || true
endif
	FORCE_COLOR=1 HYPOTHESIS_PROFILE=ci CI=true UV_NO_PROGRESS=1 .venv/bin/ruff format --check corpus_forge tests
	FORCE_COLOR=1 HYPOTHESIS_PROFILE=ci CI=true UV_NO_PROGRESS=1 .venv/bin/ruff check corpus_forge tests
	FORCE_COLOR=1 HYPOTHESIS_PROFILE=ci CI=true UV_NO_PROGRESS=1 .venv/bin/pyrefly check corpus_forge
	FORCE_COLOR=1 HYPOTHESIS_PROFILE=ci CI=true UV_NO_PROGRESS=1 .venv/bin/python -m pytest tests/unit -n auto --timeout=60 -q

docs-serve: ## Live-preview docs locally (mkdocs)
	uv run mkdocs serve

docs: ## Build docs to ./site
	uv run mkdocs build

clean:
	rm -rf .venv .pytest_cache .ruff_cache .coverage htmlcov dist build site
	find . -type d -name __pycache__ -exec rm -rf {} +