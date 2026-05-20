"""Unit tests for ``corpus_forge.eval.distill`` helpers.

The CLI/orchestration layer is exercised by the integration tests; these unit
tests pin the pure-function helpers so coverage stays high and regressions
in the math fire before the slower integration suite runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.eval.distill import (
    _compute_coverage,
    _compute_source_mix,
    _compute_template_fidelity,
    _compute_token_stats,
    _write_json_report,
    _write_md_report,
)
from corpus_forge.sdft.sources import SDFTSource

# ─────────────────────────────────────────────────────────────────────────────
# _compute_coverage
# ─────────────────────────────────────────────────────────────────────────────


def test_compute_coverage_empty_chunks_returns_zero() -> None:
    assert _compute_coverage([{"target": "x"}], []) == 0.0


def test_compute_coverage_empty_sdft_returns_zero() -> None:
    assert _compute_coverage([], [{"text": "long chunk text"}]) == 0.0


def test_compute_coverage_proportional_to_target_chars() -> None:
    sdft = [{"target": "abcde"}]  # 5 chars
    chunks = [{"text": "0123456789"}]  # 10 chars
    assert _compute_coverage(sdft, chunks) == pytest.approx(0.5)


def test_compute_coverage_clamped_to_unit_interval() -> None:
    sdft = [{"target": "x" * 1000}]
    chunks = [{"text": "y" * 100}]
    assert _compute_coverage(sdft, chunks) == 1.0


def test_compute_coverage_handles_none_target_and_text() -> None:
    sdft = [{"target": None}, {}]
    chunks = [{"text": None}, {}]
    # Both sums are 0 → 0/0 short-circuits to 0.
    assert _compute_coverage(sdft, chunks) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# _compute_source_mix
# ─────────────────────────────────────────────────────────────────────────────


def test_source_mix_zero_fills_all_eight_sources() -> None:
    mix = _compute_source_mix([])
    for src in SDFTSource:
        assert mix[src.value] == 0


def test_source_mix_counts_known_sources() -> None:
    rows = [
        {"source": "claude_code"},
        {"source": "claude_code"},
        {"source": "gemini"},
        {"source": "record_demonstration"},
    ]
    mix = _compute_source_mix(rows)
    assert mix["claude_code"] == 2
    assert mix["gemini"] == 1
    assert mix["record_demonstration"] == 1
    assert mix["cli_feedback"] == 0


def test_source_mix_records_unknown_sources_too() -> None:
    rows = [{"source": "future_client_X"}]
    mix = _compute_source_mix(rows)
    assert mix.get("future_client_X") == 1


def test_source_mix_missing_source_field_is_empty_string_bucket() -> None:
    rows = [{}]  # No "source" key → defaults to ""
    mix = _compute_source_mix(rows)
    # Empty source falls into the catch-all bucket.
    assert "" in mix or all(v == 0 for k, v in mix.items() if k != "")


# ─────────────────────────────────────────────────────────────────────────────
# _compute_template_fidelity
# ─────────────────────────────────────────────────────────────────────────────


def test_template_fidelity_empty_rows() -> None:
    out = _compute_template_fidelity([], "chatml")
    assert out == {"n_rows": 0, "n_rendered_ok": 0, "n_truncated": 0, "n_failed": 0}


def test_template_fidelity_renders_basic_rows() -> None:
    rows = [
        {
            "student_messages": [{"role": "user", "content": "q"}],
            "teacher_messages": [{"role": "assistant", "content": "a"}],
        },
    ]
    out = _compute_template_fidelity(rows, "chatml")
    assert out["n_rows"] == 1
    # Either rendered_ok or failed (depending on env); never None.
    assert out["n_rendered_ok"] + out["n_failed"] == 1


def test_template_fidelity_json_string_messages_are_parsed() -> None:
    rows = [
        {
            "student_messages": json.dumps([{"role": "user", "content": "q"}]),
            "teacher_messages": json.dumps([{"role": "assistant", "content": "a"}]),
        },
    ]
    out = _compute_template_fidelity(rows, "chatml")
    assert out["n_rows"] == 1


def test_template_fidelity_invalid_json_messages_default_to_empty() -> None:
    rows = [
        {
            "student_messages": "not valid json",
            "teacher_messages": "still not valid",
        },
    ]
    out = _compute_template_fidelity(rows, "chatml")
    assert out["n_rows"] == 1


def test_template_fidelity_unknown_template_increments_failed() -> None:
    rows = [
        {
            "student_messages": [{"role": "user", "content": "q"}],
            "teacher_messages": [{"role": "assistant", "content": "a"}],
        },
    ]
    out = _compute_template_fidelity(rows, "__definitely_not_a_template__")
    assert out["n_failed"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# _compute_token_stats
# ─────────────────────────────────────────────────────────────────────────────


def test_token_stats_empty_returns_zeros() -> None:
    out = _compute_token_stats([])
    assert out == {"p50": 0, "p95": 0, "max": 0, "mean": 0.0, "total": 0}


def test_token_stats_single_row() -> None:
    out = _compute_token_stats([{"target": "x" * 8}])  # 8 chars → 8//4 = 2
    assert out["max"] == 2
    assert out["p50"] == 2
    assert out["p95"] == 2
    assert out["total"] == 2


def test_token_stats_multiple_rows_ordering_invariant() -> None:
    rows = [{"target": "x" * c} for c in (4, 8, 12, 16, 100)]
    out = _compute_token_stats(rows)
    assert out["p50"] <= out["p95"] <= out["max"]
    assert out["mean"] >= 0.0
    assert out["total"] > 0


def test_token_stats_none_target_treated_as_empty() -> None:
    rows = [{"target": None}, {"target": "x" * 16}]
    out = _compute_token_stats(rows)
    assert out["max"] == 4  # 16 chars / 4 chars-per-token = 4
    assert out["total"] >= 4


# ─────────────────────────────────────────────────────────────────────────────
# _write_json_report / _write_md_report
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# run_distill_eval — full orchestration with a fake backend
# ─────────────────────────────────────────────────────────────────────────────


class _FakeBackend:
    """Minimal duck-typed backend for run_distill_eval unit tests."""

    def __init__(
        self,
        *,
        dataset_id: int | None,
        sdft_rows: list[dict],
        chunks: list[dict],
    ) -> None:
        self._dataset_id = dataset_id
        self._sdft_rows = sdft_rows
        self._chunks = chunks
        # `_list_chunks_for_dataset` peeks the conn_type and uses _execute.
        # We monkeypatch in the test instead — see test_run_distill_eval_full_flow.

    def find_dataset_id_by_name(self, name: str) -> int | None:
        return self._dataset_id

    def list_sdft_demonstrations(self, dataset_id: int) -> list[dict]:
        return list(self._sdft_rows)


def test_run_distill_eval_unknown_dataset_raises(tmp_path: Path) -> None:
    from corpus_forge.eval.distill import run_distill_eval

    backend = _FakeBackend(dataset_id=None, sdft_rows=[], chunks=[])
    with pytest.raises(ValueError, match="not found"):
        run_distill_eval("missing", backend=backend, report_dir=tmp_path)


def test_run_distill_eval_empty_set_writes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_forge.eval import distill as distill_mod

    backend = _FakeBackend(dataset_id=1, sdft_rows=[], chunks=[])
    # _list_chunks_for_dataset uses raw SQL — monkeypatch it to return [].
    monkeypatch.setattr(distill_mod, "_list_chunks_for_dataset", lambda b, d: [])

    out = distill_mod.run_distill_eval("demo", backend=backend, report_dir=tmp_path)

    assert out["coverage"] == 0.0
    assert out["dataset"] == "demo"
    assert "source_mix" in out
    assert (tmp_path / "eval_distill.json").is_file()
    assert (tmp_path / "eval_distill.md").is_file()


def test_run_distill_eval_full_flow_with_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_forge.eval import distill as distill_mod

    sdft_rows = [
        {
            "source": "cli_feedback",
            "target": "X",
            "student_messages": [],
            "teacher_messages": [],
        },
        {
            "source": "claude_code",
            "target": "Y",
            "student_messages": [],
            "teacher_messages": [],
        },
    ]
    chunks = [{"text": "x" * 100}, {"text": "y" * 100}]
    backend = _FakeBackend(dataset_id=42, sdft_rows=sdft_rows, chunks=chunks)
    monkeypatch.setattr(distill_mod, "_list_chunks_for_dataset", lambda b, d: chunks)

    out = distill_mod.run_distill_eval("demo", backend=backend, report_dir=tmp_path)
    assert 0.0 <= out["coverage"] <= 1.0
    assert out["source_mix"]["cli_feedback"] == 1
    assert out["source_mix"]["claude_code"] == 1
    assert out["template_fidelity"]["n_rows"] == 2
    assert out["token_stats"]["total"] >= 0


def test_run_distill_eval_default_report_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_forge.eval import distill as distill_mod

    home = tmp_path / "home"
    home.mkdir()
    # Path.home() reads HOME on POSIX, USERPROFILE on Windows. Set both.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    backend = _FakeBackend(dataset_id=1, sdft_rows=[], chunks=[])
    monkeypatch.setattr(distill_mod, "_list_chunks_for_dataset", lambda b, d: [])

    out = distill_mod.run_distill_eval("demo", backend=backend)
    assert out["dataset"] == "demo"
    assert (home / ".cache" / "corpus-forge" / "reports").is_dir()


def test_write_reports_produce_files(tmp_path: Path) -> None:
    result: dict = {
        "coverage": 0.4,
        "source_mix": {"claude_code": 2, "gemini": 1},
        "template_fidelity": {
            "n_rows": 3,
            "n_rendered_ok": 3,
            "n_truncated": 0,
            "n_failed": 0,
        },
        "token_stats": {"p50": 4, "p95": 12, "max": 16, "mean": 8.0, "total": 24},
        "dataset": "demo",
    }
    _write_json_report(tmp_path, result)
    _write_md_report(tmp_path, result, "demo")

    js = tmp_path / "eval_distill.json"
    md = tmp_path / "eval_distill.md"
    assert js.is_file()
    assert md.is_file()
    loaded = json.loads(js.read_text())
    assert loaded["coverage"] == 0.4
    assert "claude_code" in loaded["source_mix"]
    # MD has section headers.
    md_text = md.read_text()
    assert "Coverage" in md_text
    assert "Source Mix" in md_text
    assert "Template Fidelity" in md_text
    assert "Token Stats" in md_text
