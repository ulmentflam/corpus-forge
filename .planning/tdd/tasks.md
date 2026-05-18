# TDD Task Board — v0.1.0b3 Windows green + coverage push

_Owner: tdd-principal (inline execution)._
_Date: 2026-05-18._

Brief: Make CI green on Windows (3 jobs failing on pre-existing portability
bugs); push coverage from 91.05% → ≥93% if possible without contortions.

## Project gates
- format: `uv run ruff format`
- lint: `uv run ruff check`
- typecheck: `uv run pyrefly check corpus_forge`
- test: `uv run python -m pytest`
- coverage-min: 85 (target 93)

## Tasks
| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W1 | Fix redact_file Windows encoding (test side) | — | tests/diagnostics/test_redact.py | low | done | inline | utf-8 read on write_text/read_text |
| W2 | Skip POSIX-perm test on Windows | — | tests/unit/test_corpusignore.py | low | done | inline | @pytest.mark.skipif sys.platform=="win32" |
| W3 | Skip SIGINT-to-xdist test on Windows | — | tests/diagnostics/test_logs_subcommand.py | low | done | inline | @pytest.mark.skipif |
| W4 | service.stop_daemon Windows-safe + test guard | — | corpus_forge/admin/service.py, tests/admin/test_service_lifecycle.py | med | done | inline | _SIGKILL constant abstracts win32 |
| W5 | Atomic-write Windows-safe + test race patch | — | corpus_forge/embedders/_marker.py, tests/embedders/test_marker.py | med | done | inline | os.replace retry on Windows PermissionError |
| C1 | dataset.py coverage push | W1-W5 | tests/admin/test_dataset_source_crud.py | low | done | inline | drop-vectors + branches |
| C2 | source.py coverage push | W1-W5 | tests/admin/test_dataset_source_crud.py | low | done | inline | non-filesystem plugins + ingest path |
| C3 | foreground.py coverage push | W1-W5 | tests/admin/test_foreground.py | low | done | inline | KeyboardInterrupt + safe_std branches |
| C4 | fingerprint.py coverage push | W1-W5 | tests/embedders/test_fingerprint.py | low | done | inline | edge JSON / missing-helpers |

## DAG
- Wave 0: W1, W2, W3, W4, W5 (parallel — disjoint surfaces) — DONE
- Wave 1: C1, C2, C3, C4 — DONE
