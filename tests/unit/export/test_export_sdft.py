"""Q4-T1 RED — Unit tests for export_sdft.

``export_sdft(dataset, template, out_path, format, *, backend, held_out_fraction,
include_sources)`` reads ``sdft_demonstrations`` rows and writes JSONL or Parquet
with rows:
    {query, student_messages, teacher_messages, target, source, dataset_id, template}

Produces optional train/held_out splits when ``held_out_fraction > 0``, and
filters by source values when ``include_sources`` is supplied.

Returns::

    {"row_count": int, "train_count": int, "held_out_count": int, "out_paths": list[str]}

RED state
---------
``corpus_forge.export.export_sdft`` does not exist yet.  Every test that calls
``from corpus_forge.export import export_sdft`` fails immediately with::

    ImportError: cannot import name 'export_sdft' from 'corpus_forge.export'

Run command::

    uv run pytest tests/unit/export/test_export_sdft.py -x 2>&1 | tail -40
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Import the target function — will fail ImportError → RED
# ---------------------------------------------------------------------------
from corpus_forge.export import export_sdft  # type: ignore[attr-defined]

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Required row schema keys for SDFT export
# ---------------------------------------------------------------------------

_REQUIRED_SDFT_KEYS = {
    "query",
    "student_messages",
    "teacher_messages",
    "target",
    "source",
    "dataset_id",
    "template",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _insert_dataset(backend: SQLiteBackend, name: str = "q4-ds") -> int:
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (name, "chat", "Q4 unit test dataset"),
        ).fetchone()[0]
        conn.commit()
    return ds_id


def _insert_sdft_row(
    backend: SQLiteBackend,
    dataset_id: int,
    *,
    query: str = "What is X?",
    student_messages: list[dict] | None = None,
    teacher_messages: list[dict] | None = None,
    target: str = "X is the answer.",
    source: str = "cli_feedback",
    trace_id: str | None = None,
) -> int:
    """Insert one sdft_demonstrations row directly via raw connection."""
    import hashlib

    if student_messages is None:
        student_messages = [{"role": "assistant", "content": "I think X means something."}]
    if teacher_messages is None:
        teacher_messages = [{"role": "user", "content": "Please correct this."}]

    import json as _json

    student_json = _json.dumps(student_messages)
    teacher_json = _json.dumps(teacher_messages)

    payload = _json.dumps(
        [query, student_messages, teacher_messages, target],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    with backend._get_connection() as conn:
        row_id = conn.execute(
            """
            INSERT INTO sdft_demonstrations
              (dataset_id, query, student_messages, teacher_messages,
               target, source, trace_id, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                dataset_id,
                query,
                student_json,
                teacher_json,
                target,
                source,
                trace_id,
                content_hash,
            ),
        ).fetchone()[0]
        conn.commit()
    return row_id


def _insert_sdft_row_with_hash(
    backend: SQLiteBackend,
    dataset_id: int,
    *,
    query: str,
    content_hash: str,
) -> None:
    """Insert one sdft row with an EXPLICIT content_hash.

    The default ``_insert_sdft_row`` derives content_hash from the payload;
    the held-out-bucketing test needs to control the hash so the split
    bucket (sha256(content_hash) % 100) is deterministic and known.
    """
    with backend._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO sdft_demonstrations
              (dataset_id, query, student_messages, teacher_messages,
               target, source, trace_id, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                query,
                json.dumps([{"role": "assistant", "content": "s"}]),
                json.dumps([{"role": "user", "content": "t"}]),
                "tgt",
                "cli_feedback",
                None,
                content_hash,
            ),
        )
        conn.commit()


def _seed_three_rows(backend: SQLiteBackend, dataset_id: int) -> list[int]:
    """Seed 3 rows with distinct queries/targets for split/filter tests."""
    ids = []
    for i in range(3):
        row_id = _insert_sdft_row(
            backend,
            dataset_id,
            query=f"Query {i}",
            target=f"Target answer {i}",
            source="cli_feedback",
            student_messages=[{"role": "assistant", "content": f"Student response {i}"}],
            teacher_messages=[{"role": "user", "content": f"Teacher correction {i}"}],
        )
        ids.append(row_id)
    return ids


# ===========================================================================
# Basic JSONL export
# ===========================================================================


class TestExportSdftJsonl:
    def test_basic_jsonl_export_creates_file(self, tmp_path: Path) -> None:
        """A single-row dataset produces a JSONL file at out_path."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
        )

        assert out.exists(), "output file must be created"
        assert result["row_count"] == 1

    def test_basic_jsonl_export_row_count_matches_seed(self, tmp_path: Path) -> None:
        """Row count in returned dict matches lines in output file."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _seed_three_rows(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out)
        assert len(rows) == 3
        assert result["row_count"] == 3

    def test_jsonl_row_schema_contains_all_required_keys(self, tmp_path: Path) -> None:
        """Each JSONL row contains exactly the seven documented keys."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        rows = _read_jsonl(out)
        assert rows, "expected at least one row"
        for row in rows:
            missing = _REQUIRED_SDFT_KEYS - set(row.keys())
            assert not missing, f"Row missing required keys: {missing}; row keys: {set(row.keys())}"

    def test_jsonl_row_schema_has_no_extra_undocumented_keys(self, tmp_path: Path) -> None:
        """Exported rows must not have keys beyond the documented schema."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        rows = _read_jsonl(out)
        for row in rows:
            extra = set(row.keys()) - _REQUIRED_SDFT_KEYS
            assert not extra, f"Row has undocumented extra keys: {extra}"


# ===========================================================================
# Basic Parquet export
# ===========================================================================


class TestExportSdftParquet:
    def test_basic_parquet_export_creates_file(self, tmp_path: Path) -> None:
        """A parquet file is created at out_path when format='parquet'."""
        pytest.importorskip("pyarrow", reason="pyarrow not installed")
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.parquet"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="parquet",
            backend=backend,
        )

        assert out.exists(), "parquet output file must be created"
        assert result["row_count"] == 1

    def test_parquet_round_trips_via_datasets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HF datasets.load_dataset('parquet', ...) can read the produced file."""
        pytest.importorskip("pyarrow", reason="pyarrow not installed")
        datasets_lib = pytest.importorskip("datasets", reason="datasets not installed")
        # The default HF cache (~/.cache/huggingface) may be owned by root
        # on dev machines / CI; point every relevant env var at a per-test
        # tmp dir so ``load_dataset`` can create its cache without a perms
        # collision.  ``HF_HOME`` covers the umbrella default;
        # ``HF_DATASETS_CACHE`` is the dedicated datasets-cache override.
        hf_cache = tmp_path / "hf_cache"
        monkeypatch.setenv("HF_HOME", str(hf_cache))
        monkeypatch.setenv("HF_DATASETS_CACHE", str(hf_cache / "datasets"))
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _seed_three_rows(backend, ds_id)
        out = tmp_path / "sdft.parquet"

        export_sdft("q4-ds", "chatml", out, format="parquet", backend=backend)

        ds = datasets_lib.load_dataset(
            "parquet",
            data_files=str(out),
            split="train",
            cache_dir=str(hf_cache / "datasets"),
        )
        assert len(ds) == 3
        assert set(_REQUIRED_SDFT_KEYS).issubset(set(ds.column_names))


# ===========================================================================
# Empty dataset
# ===========================================================================


class TestExportSdftEmpty:
    def test_empty_dataset_creates_empty_file(self, tmp_path: Path) -> None:
        """An empty dataset produces a file with zero rows and row_count=0."""
        backend = _make_backend()
        _insert_dataset(backend)
        out = tmp_path / "sdft_empty.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
        )

        assert out.exists(), "output file must exist even for empty dataset"
        rows = _read_jsonl(out)
        assert rows == [], f"expected 0 rows; got {rows}"
        assert result["row_count"] == 0
        assert result["train_count"] == 0
        assert result["held_out_count"] == 0

    def test_empty_dataset_returns_zero_counts(self, tmp_path: Path) -> None:
        """Return dict has all-zero counts for empty dataset."""
        backend = _make_backend()
        _insert_dataset(backend)
        out = tmp_path / "sdft_empty.jsonl"

        result = export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        assert result == {
            "row_count": 0,
            "train_count": 0,
            "held_out_count": 0,
            "out_paths": [str(out)],
        } or (
            result["row_count"] == 0
            and result["train_count"] == 0
            and result["held_out_count"] == 0
        )


# ===========================================================================
# Held-out split
# ===========================================================================


class TestExportSdftHeldOutSplit:
    def test_held_out_fraction_produces_two_files(self, tmp_path: Path) -> None:
        """held_out_fraction=0.1 with enough rows produces two files."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        # Seed 20 rows so a 0.1 split gives ~2 held-out rows
        for i in range(20):
            _insert_sdft_row(
                backend,
                ds_id,
                query=f"Question {i}",
                target=f"Answer {i}",
                source="cli_feedback",
            )
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            held_out_fraction=0.1,
        )

        train_path = tmp_path / "sdft.train.jsonl"
        held_out_path = tmp_path / "sdft.held_out.jsonl"
        assert train_path.exists(), "train file must be created"
        assert held_out_path.exists(), "held_out file must be created"
        assert result["train_count"] + result["held_out_count"] == result["row_count"]

    def test_held_out_fraction_splits_are_disjoint(self, tmp_path: Path) -> None:
        """No row appears in both train and held_out files."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        for i in range(20):
            _insert_sdft_row(
                backend,
                ds_id,
                query=f"Q {i}",
                target=f"A {i}",
                source="cli_feedback",
            )
        out = tmp_path / "sdft.jsonl"

        export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            held_out_fraction=0.1,
        )

        train_rows = _read_jsonl(tmp_path / "sdft.train.jsonl")
        held_rows = _read_jsonl(tmp_path / "sdft.held_out.jsonl")
        train_queries = {r["query"] for r in train_rows}
        held_queries = {r["query"] for r in held_rows}
        overlap = train_queries & held_queries
        assert not overlap, f"train and held_out overlap on queries: {overlap}"

    def test_held_out_split_is_deterministic(self, tmp_path: Path) -> None:
        """Same content produces identical splits on two consecutive calls."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        for i in range(20):
            _insert_sdft_row(
                backend,
                ds_id,
                query=f"Query {i}",
                target=f"Target {i}",
                source="cli_feedback",
            )

        out_a = tmp_path / "run_a.jsonl"
        out_b = tmp_path / "run_b.jsonl"

        export_sdft(
            "q4-ds", "chatml", out_a, format="jsonl", backend=backend, held_out_fraction=0.2
        )
        export_sdft(
            "q4-ds", "chatml", out_b, format="jsonl", backend=backend, held_out_fraction=0.2
        )

        train_a = _read_jsonl(tmp_path / "run_a.train.jsonl")
        train_b = _read_jsonl(tmp_path / "run_b.train.jsonl")
        held_a = _read_jsonl(tmp_path / "run_a.held_out.jsonl")
        held_b = _read_jsonl(tmp_path / "run_b.held_out.jsonl")

        assert [r["query"] for r in train_a] == [r["query"] for r in train_b], (
            "train split must be deterministic across calls"
        )
        assert [r["query"] for r in held_a] == [r["query"] for r in held_b], (
            "held_out split must be deterministic across calls"
        )

    def test_no_split_when_held_out_fraction_zero(self, tmp_path: Path) -> None:
        """held_out_fraction=0.0 (default) writes a single file only."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _seed_three_rows(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            held_out_fraction=0.0,
        )

        assert out.exists(), "output file must exist"
        assert not (tmp_path / "sdft.train.jsonl").exists(), (
            "train split file must NOT be created when held_out_fraction=0"
        )
        assert result["held_out_count"] == 0
        assert result["train_count"] == result["row_count"]

    def test_held_out_split_parquet_uses_correct_suffixes(self, tmp_path: Path) -> None:
        """Parquet split files use .train.parquet / .held_out.parquet suffixes."""
        pytest.importorskip("pyarrow", reason="pyarrow not installed")
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        for i in range(20):
            _insert_sdft_row(backend, ds_id, query=f"Q {i}", target=f"A {i}")
        out = tmp_path / "sdft.parquet"

        export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="parquet",
            backend=backend,
            held_out_fraction=0.1,
        )

        assert (tmp_path / "sdft.train.parquet").exists(), "train.parquet file must exist"
        assert (tmp_path / "sdft.held_out.parquet").exists(), "held_out.parquet file must exist"

    def test_held_out_bucketing_routes_by_sha256_mod_100(self, tmp_path: Path) -> None:
        """Pin the split RULE, not just its determinism/disjointness.

        held iff ``int(sha256(content_hash), 16) % 100 < int(fraction * 100)``.
        frac=0.1 → threshold=10; ``hash-3`` → bucket 3 (held), ``hash-0`` →
        bucket 41 (train). The other held-out tests only assert the split is
        deterministic + disjoint — those survive an inverted ``<``/``>=``, a
        swapped hash, or a changed modulus, which would silently LEAK held-out
        eval rows into the training set (ML data leakage in the HF deliverable
        with no error). This test is the only one that nails the routing.
        """
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row_with_hash(backend, ds_id, query="held-query", content_hash="hash-3")
        _insert_sdft_row_with_hash(backend, ds_id, query="train-query", content_hash="hash-0")

        out = tmp_path / "sdft.jsonl"
        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            held_out_fraction=0.1,
        )

        held = [r["query"] for r in _read_jsonl(tmp_path / "sdft.held_out.jsonl")]
        train = [r["query"] for r in _read_jsonl(tmp_path / "sdft.train.jsonl")]
        assert held == ["held-query"], f"low-bucket hash must route to held_out, got {held}"
        assert train == ["train-query"], f"high-bucket hash must route to train, got {train}"
        assert result["held_out_count"] == 1
        assert result["train_count"] == 1


# ===========================================================================
# Source filtering
# ===========================================================================


class TestExportSdftSourceFilter:
    def test_include_sources_single_filters_other_sources(self, tmp_path: Path) -> None:
        """include_sources=['cli_feedback'] excludes rows with other sources."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id, query="Q-cli", source="cli_feedback")
        _insert_sdft_row(backend, ds_id, query="Q-gemini", source="gemini")
        _insert_sdft_row(backend, ds_id, query="Q-codex", source="codex")
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            include_sources=["cli_feedback"],
        )

        rows = _read_jsonl(out)
        assert len(rows) == 1, f"expected 1 row; got {len(rows)}"
        assert rows[0]["source"] == "cli_feedback"
        assert result["row_count"] == 1

    def test_include_sources_multiple_includes_each_matching_source(self, tmp_path: Path) -> None:
        """include_sources=['claude_code','gemini'] keeps both, excludes others."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id, query="Q-cc", source="claude_code")
        _insert_sdft_row(backend, ds_id, query="Q-gemini", source="gemini")
        _insert_sdft_row(backend, ds_id, query="Q-cli", source="cli_feedback")
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            include_sources=["claude_code", "gemini"],
        )

        rows = _read_jsonl(out)
        assert len(rows) == 2, f"expected 2 rows; got {len(rows)}"
        assert {r["source"] for r in rows} == {"claude_code", "gemini"}
        assert result["row_count"] == 2

    def test_include_sources_none_returns_all_rows(self, tmp_path: Path) -> None:
        """include_sources=None (default) includes rows from all sources."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        for source in ["cli_feedback", "gemini", "curation_commit", "codex"]:
            _insert_sdft_row(backend, ds_id, query=f"Q-{source}", source=source)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        rows = _read_jsonl(out)
        assert len(rows) == 4
        assert result["row_count"] == 4

    def test_include_sources_empty_list_produces_zero_rows(self, tmp_path: Path) -> None:
        """include_sources=[] (empty list) should export no rows."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id, query="Q1", source="cli_feedback")
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            include_sources=[],
        )

        rows = _read_jsonl(out)
        assert rows == [], f"expected 0 rows for empty include_sources; got {rows}"
        assert result["row_count"] == 0


# ===========================================================================
# Template rendering
# ===========================================================================


class TestExportSdftTemplateRendering:
    def test_chatml_template_renders_student_messages(self, tmp_path: Path) -> None:
        """With template='chatml', student/teacher messages are rendered through ChatML."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(
            backend,
            ds_id,
            query="What is 2+2?",
            student_messages=[{"role": "assistant", "content": "I think it is 5."}],
            teacher_messages=[{"role": "user", "content": "Actually it is 4."}],
            target="4",
            source="cli_feedback",
        )
        out = tmp_path / "sdft.jsonl"

        export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        rows = _read_jsonl(out)
        assert rows, "expected at least one row"
        row = rows[0]
        # student_messages and teacher_messages must be the message lists
        # (the rendered text may appear under a separate 'text' key or be embedded)
        assert "student_messages" in row
        assert "teacher_messages" in row
        assert isinstance(row["student_messages"], list)
        assert isinstance(row["teacher_messages"], list)

    def test_custom_jinja_template_is_accepted(self, tmp_path: Path) -> None:
        """A custom Jinja2 template string can be passed as the template argument."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id, query="Hello", target="Hi", source="cli_feedback")
        out = tmp_path / "sdft.jsonl"

        custom_jinja = "MSG:{{ messages | length }}"
        # Should not raise — custom jinja is passed through for rendering
        result = export_sdft(
            "q4-ds",
            custom_jinja,
            out,
            format="jsonl",
            backend=backend,
            custom_jinja=custom_jinja,
        )

        assert result["row_count"] == 1

    def test_template_name_stored_in_row(self, tmp_path: Path) -> None:
        """The exported row's 'template' field matches the template argument passed in."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        rows = _read_jsonl(out)
        assert rows[0]["template"] == "chatml"


# ===========================================================================
# Return value contract
# ===========================================================================


class TestExportSdftReturnValue:
    def test_return_value_keys(self, tmp_path: Path) -> None:
        """Return dict contains exactly row_count, train_count, held_out_count, out_paths."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        required = {"row_count", "train_count", "held_out_count", "out_paths"}
        assert required.issubset(set(result.keys())), (
            f"return dict missing keys: {required - set(result.keys())}"
        )

    def test_return_value_out_paths_is_list_of_strings(self, tmp_path: Path) -> None:
        """out_paths in return value is a list of strings."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        assert isinstance(result["out_paths"], list)
        for p in result["out_paths"]:
            assert isinstance(p, str), f"out_paths must contain strings; got {type(p)}"

    def test_no_split_out_paths_has_single_entry(self, tmp_path: Path) -> None:
        """Without split, out_paths has exactly one entry equal to str(out_path)."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        result = export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        assert len(result["out_paths"]) == 1
        assert result["out_paths"][0] == str(out)

    def test_with_split_out_paths_has_two_entries(self, tmp_path: Path) -> None:
        """With held_out_fraction>0, out_paths has two entries: train + held_out."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        for i in range(20):
            _insert_sdft_row(backend, ds_id, query=f"Q {i}", target=f"A {i}")
        out = tmp_path / "sdft.jsonl"

        result = export_sdft(
            "q4-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            held_out_fraction=0.1,
        )

        assert len(result["out_paths"]) == 2

    def test_unknown_dataset_raises(self, tmp_path: Path) -> None:
        """Requesting a dataset that doesn't exist raises ValueError."""
        backend = _make_backend()
        out = tmp_path / "sdft.jsonl"

        with pytest.raises((ValueError, KeyError)):
            export_sdft("nonexistent-dataset", "chatml", out, format="jsonl", backend=backend)


# ===========================================================================
# HF Datasets round-trip (JSONL)
# ===========================================================================


class TestExportSdftHFRoundTrip:
    def test_jsonl_loads_via_hf_datasets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """datasets.load_dataset('json', data_files=...) can parse the JSONL output."""
        datasets_lib = pytest.importorskip("datasets", reason="datasets not installed")
        # Same HF-cache-perms guard as the parquet round-trip test above —
        # ``~/.cache/huggingface`` may be root-owned on dev machines / CI.
        hf_cache = tmp_path / "hf_cache"
        monkeypatch.setenv("HF_HOME", str(hf_cache))
        monkeypatch.setenv("HF_DATASETS_CACHE", str(hf_cache / "datasets"))
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _seed_three_rows(backend, ds_id)
        out = tmp_path / "sdft.jsonl"

        export_sdft("q4-ds", "chatml", out, format="jsonl", backend=backend)

        hf_ds = datasets_lib.load_dataset(
            "json",
            data_files=str(out),
            split="train",
            cache_dir=str(hf_cache / "datasets"),
        )
        assert len(hf_ds) == 3
        for col in _REQUIRED_SDFT_KEYS:
            assert col in hf_ds.column_names, f"HF dataset missing column {col!r}"
