"""Q4-T1 RED — Golden-file regression tests for export_chat and export_feedback_pairs.

These tests pin the row shapes produced by the two EXISTING exporters so that
the coder implementing ``export_sdft`` cannot accidentally modify them.

How the baselines work
----------------------
On first run (when the baseline files are absent) the test generates them by
running the current exporters against a small in-memory fixture corpus and
writes the output to ``tests/fixtures/export/``.  The files are then committed
as checked-in fixtures.

On subsequent runs the test compares each exporter's fresh output against the
committed baseline using line-by-line JSON comparison (so irrelevant whitespace
differences don't cause false failures, but any schema change does).

RED state
---------
The import of ``export_chat`` and ``export_feedback_pairs`` from
``corpus_forge.export`` succeeds (both are already implemented).  These tests
are expected to be GREEN against the CURRENT code.  They turn RED only if the
coder modifies the row schema.

The fixture corpus is deterministic: timestamps are replaced by a fixed
sentinel before comparison so clock drift never causes false failures.

Run command::

    uv run pytest tests/unit/export/test_chat_export_unchanged.py -v 2>&1 | tail -30
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.export import export_chat, export_feedback_pairs

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixture baseline paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "export"
_CHAT_BASELINE = _FIXTURES_DIR / "export_chat_baseline.jsonl"
_FEEDBACK_BASELINE = _FIXTURES_DIR / "export_feedback_pairs_baseline.jsonl"

# Sentinel string used to normalise all timestamps before comparison.
_TS_SENTINEL = "2000-01-01T00:00:00+00:00"

# Pattern that matches timestamps in both ISO-8601 (with T separator) and
# SQLite's space-separated format (YYYY-MM-DD HH:MM:SS), with optional
# fractional seconds and optional timezone offset.
_TS_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Replace timestamps with a fixed sentinel so rows are comparison-stable."""
    serialised = json.dumps(row, ensure_ascii=False, default=str)
    normalised = _TS_PATTERN.sub(_TS_SENTINEL, serialised)
    return json.loads(normalised)


def _normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_normalise_row(r) for r in rows]


def _seed_chat_corpus(backend: SQLiteBackend) -> dict[str, Any]:
    """Seed a minimal, deterministic corpus for golden export tests."""
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("golden-ds", "chat", "Golden-file regression dataset"),
        ).fetchone()[0]

        conv_id = conn.execute(
            "INSERT INTO conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (ds_id, "test://golden/conv1", "hash-golden-1", "Golden Conversation", 0, "{}"),
        ).fetchone()[0]

        # Two messages: one user, one assistant
        ts_base = datetime(2000, 1, 1, 12, 0, 0, tzinfo=UTC)
        for i, (role, content) in enumerate(
            [("user", "What is corpus-forge?"), ("assistant", "It is a corpus tool.")]
        ):
            ts = (ts_base + timedelta(seconds=i)).isoformat()
            conn.execute(
                "INSERT INTO messages"
                " (conversation_id, turn_index, role, content, ts)"
                " VALUES (?, ?, ?, ?, ?) RETURNING id",
                (conv_id, i, role, content, ts),
            ).fetchone()

        conn.commit()

    return {"dataset_id": ds_id, "dataset_name": "golden-ds", "conv_id": conv_id}


def _seed_feedback_corpus(backend: SQLiteBackend) -> dict[str, Any]:
    """Seed conversation + feedback_session + 2 events for feedback export golden tests."""
    ids = _seed_chat_corpus(backend)

    fs_id = backend.upsert_feedback_session(
        client="claude-code",
        session_id="golden-session-001",
        host="golden-host",
        started_at=datetime(2000, 1, 1, 11, 0, 0, tzinfo=UTC).isoformat(),
    )
    backend.link_feedback_session_to_conversation(
        "claude-code", "golden-session-001", ids["conv_id"]
    )

    # One audit event
    audit_id = backend.audit_event(
        "golden-host",
        "claude-code",
        "golden-session-001",
        "add_label",
        "conversation",
        ids["conv_id"],
        {"before": "unlabeled"},
        {"after": "labeled"},
        False,
    )

    # One feedback event
    feedback_id = backend.add_feedback(
        "conversation",
        ids["conv_id"],
        kind="thumbs",
        rating=1,
        text="Good example.",
    )

    backend.append_feedback_event(
        fs_id,
        audit_id=audit_id,
        feedback_id=None,
        entity_type="conversation",
        entity_id=ids["conv_id"],
    )
    backend.append_feedback_event(
        fs_id,
        audit_id=None,
        feedback_id=feedback_id,
        entity_type="conversation",
        entity_id=ids["conv_id"],
    )

    return {**ids, "fs_id": fs_id, "audit_id": audit_id, "feedback_id": feedback_id}


def _generate_chat_baseline(out_path: Path) -> None:
    """Generate the export_chat baseline from the current implementation."""
    backend = _make_backend()
    _seed_chat_corpus(backend)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    export_chat("golden-ds", "chatml", out_path, format="jsonl", backend=backend)


def _generate_feedback_baseline(out_path: Path) -> None:
    """Generate the export_feedback_pairs baseline from the current implementation."""
    backend = _make_backend()
    _seed_feedback_corpus(backend)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    export_feedback_pairs("golden-ds", "chatml", out_path, format="jsonl", backend=backend)


# ---------------------------------------------------------------------------
# Auto-generate baselines if absent (first-run bootstrap)
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """Generate baseline fixtures when they don't exist yet."""
    if not _CHAT_BASELINE.exists():
        _generate_chat_baseline(_CHAT_BASELINE)
    if not _FEEDBACK_BASELINE.exists():
        _generate_feedback_baseline(_FEEDBACK_BASELINE)


# Call baseline generation at module import time so the files exist
# before any test in this module runs.  pytest_configure hooks are only
# called when pytest is running, so we bootstrap here for direct imports.
_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
if not _CHAT_BASELINE.exists():
    _generate_chat_baseline(_CHAT_BASELINE)
if not _FEEDBACK_BASELINE.exists():
    _generate_feedback_baseline(_FEEDBACK_BASELINE)


# ===========================================================================
# export_chat golden-file regression
# ===========================================================================


class TestExportChatGoldenFile:
    def test_export_chat_row_count_matches_baseline(self, tmp_path: Path) -> None:
        """Fresh export_chat run produces the same number of rows as the baseline."""
        backend = _make_backend()
        _seed_chat_corpus(backend)
        out = tmp_path / "chat_fresh.jsonl"

        export_chat("golden-ds", "chatml", out, format="jsonl", backend=backend)

        fresh_rows = _normalise_rows(_read_jsonl(out))
        baseline_rows = _normalise_rows(_read_jsonl(_CHAT_BASELINE))
        assert len(fresh_rows) == len(baseline_rows), (
            f"export_chat row count changed: baseline={len(baseline_rows)}, fresh={len(fresh_rows)}"
        )

    def test_export_chat_row_schema_matches_baseline(self, tmp_path: Path) -> None:
        """Fresh export_chat rows have the same keys as the baseline rows."""
        backend = _make_backend()
        _seed_chat_corpus(backend)
        out = tmp_path / "chat_fresh.jsonl"

        export_chat("golden-ds", "chatml", out, format="jsonl", backend=backend)

        fresh_rows = _read_jsonl(out)
        baseline_rows = _read_jsonl(_CHAT_BASELINE)
        assert fresh_rows, "fresh output must be non-empty"
        assert baseline_rows, "baseline must be non-empty"

        for i, (fresh, base) in enumerate(zip(fresh_rows, baseline_rows, strict=True)):
            assert set(fresh.keys()) == set(base.keys()), (
                f"Row {i} key mismatch: fresh={sorted(fresh.keys())}, "
                f"baseline={sorted(base.keys())}"
            )

    def test_export_chat_row_content_matches_baseline(self, tmp_path: Path) -> None:
        """Normalised row content is byte-identical to the committed baseline."""
        backend = _make_backend()
        _seed_chat_corpus(backend)
        out = tmp_path / "chat_fresh.jsonl"

        export_chat("golden-ds", "chatml", out, format="jsonl", backend=backend)

        fresh_rows = _normalise_rows(_read_jsonl(out))
        baseline_rows = _normalise_rows(_read_jsonl(_CHAT_BASELINE))

        assert fresh_rows == baseline_rows, (
            "export_chat schema changed vs committed baseline.\n"
            'If this is intentional, regenerate: python -c "'
            "from tests.unit.export.test_chat_export_unchanged import "
            "_generate_chat_baseline; from pathlib import Path; "
            "_generate_chat_baseline(Path('tests/fixtures/export/export_chat_baseline.jsonl'))\""
        )

    def test_export_chat_baseline_has_required_keys(self) -> None:
        """The committed baseline has all required export_chat row keys."""
        baseline_rows = _read_jsonl(_CHAT_BASELINE)
        assert baseline_rows, "baseline must not be empty"
        required = {
            "conversation_id",
            "title",
            "source_uri",
            "description",
            "template",
            "model_id",
            "text",
            "message_count",
            "messages",
        }
        for row in baseline_rows:
            missing = required - set(row.keys())
            assert not missing, f"baseline row missing required keys: {missing}"


# ===========================================================================
# export_feedback_pairs golden-file regression
# ===========================================================================


class TestExportFeedbackPairsGoldenFile:
    def test_export_feedback_pairs_row_count_matches_baseline(self, tmp_path: Path) -> None:
        """Fresh export_feedback_pairs run produces the same row count as the baseline."""
        backend = _make_backend()
        _seed_feedback_corpus(backend)
        out = tmp_path / "feedback_fresh.jsonl"

        export_feedback_pairs("golden-ds", "chatml", out, format="jsonl", backend=backend)

        fresh_rows = _normalise_rows(_read_jsonl(out))
        baseline_rows = _normalise_rows(_read_jsonl(_FEEDBACK_BASELINE))
        assert len(fresh_rows) == len(baseline_rows), (
            f"export_feedback_pairs row count changed: baseline={len(baseline_rows)}, "
            f"fresh={len(fresh_rows)}"
        )

    def test_export_feedback_pairs_row_schema_matches_baseline(self, tmp_path: Path) -> None:
        """Fresh export_feedback_pairs rows have the same keys as the baseline."""
        backend = _make_backend()
        _seed_feedback_corpus(backend)
        out = tmp_path / "feedback_fresh.jsonl"

        export_feedback_pairs("golden-ds", "chatml", out, format="jsonl", backend=backend)

        fresh_rows = _read_jsonl(out)
        baseline_rows = _read_jsonl(_FEEDBACK_BASELINE)
        assert fresh_rows, "fresh output must be non-empty"

        for i, (fresh, base) in enumerate(zip(fresh_rows, baseline_rows, strict=False)):
            assert set(fresh.keys()) == set(base.keys()), (
                f"Row {i} key mismatch: fresh={sorted(fresh.keys())}, "
                f"baseline={sorted(base.keys())}"
            )

    def test_export_feedback_pairs_content_matches_baseline(self, tmp_path: Path) -> None:
        """Normalised content is byte-identical to the committed baseline."""
        backend = _make_backend()
        _seed_feedback_corpus(backend)
        out = tmp_path / "feedback_fresh.jsonl"

        export_feedback_pairs("golden-ds", "chatml", out, format="jsonl", backend=backend)

        fresh_rows = _normalise_rows(_read_jsonl(out))
        baseline_rows = _normalise_rows(_read_jsonl(_FEEDBACK_BASELINE))

        assert fresh_rows == baseline_rows, (
            "export_feedback_pairs schema changed vs committed baseline.\n"
            "If this is intentional, regenerate the baseline fixture."
        )

    def test_export_feedback_pairs_baseline_has_required_keys(self) -> None:
        """The committed baseline has all required export_feedback_pairs row keys."""
        baseline_rows = _read_jsonl(_FEEDBACK_BASELINE)
        assert baseline_rows, "baseline must not be empty"
        required = {
            "feedback_event_id",
            "feedback_session_id",
            "client",
            "session_id",
            "host",
            "prompt",
            "response",
            "after",
            "kind",
            "ts",
        }
        for row in baseline_rows:
            missing = required - set(row.keys())
            assert not missing, f"baseline row missing required keys: {missing}"
