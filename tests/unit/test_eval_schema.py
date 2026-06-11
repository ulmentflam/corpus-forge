"""Unit tests for ``corpus_forge.eval._schema.EvalOutput``.

Backs RFC ``rfc-eval-framework-expansion`` (P1). The envelope is the
contract that downstream dashboards read; we pin (a) the literal field
names, (b) the literal kinds the envelope accepts, (c) the
serialisation shape, and (d) the rejection of typos via
``extra='forbid'`` so future evaluators can't accidentally widen the
envelope by accident.
"""

from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from corpus_forge.eval._schema import EvalKind, EvalOutput, _utc_now_iso


class TestEvalOutputConstruction:
    """The envelope accepts each documented kind + defaults sensibly."""

    @pytest.mark.parametrize(
        "kind", ["classifier", "quality", "retrieval", "regression", "embedder_ranking"]
    )
    def test_accepts_each_documented_kind(self, kind: str) -> None:
        out = EvalOutput(eval_kind=kind, dataset="ds")  # type: ignore[arg-type]
        assert out.eval_kind == kind
        assert out.dataset == "ds"

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalOutput(eval_kind="custom-kind", dataset="ds")  # type: ignore[arg-type]

    def test_dataset_required(self) -> None:
        with pytest.raises(ValidationError):
            EvalOutput(eval_kind="retrieval")  # type: ignore[call-arg]

    def test_defaults_to_empty_dicts(self) -> None:
        out = EvalOutput(eval_kind="retrieval", dataset="ds")
        assert out.metrics == {}
        assert out.config == {}

    def test_git_commit_default_is_none(self) -> None:
        out = EvalOutput(eval_kind="retrieval", dataset="ds")
        assert out.git_commit is None

    def test_ts_default_is_iso_8601_z(self) -> None:
        out = EvalOutput(eval_kind="retrieval", dataset="ds")
        # ISO 8601 like "2026-05-22T12:34:56.789012Z".
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z",
            out.ts,
        ), f"unexpected ts format: {out.ts!r}"


class TestEvalOutputSerialisation:
    """The envelope round-trips through JSON unchanged."""

    def test_dump_json_top_level_keys(self) -> None:
        out = EvalOutput(
            eval_kind="classifier",
            dataset="ds1",
            git_commit="abc123",
            ts="2026-05-22T00:00:00Z",
            metrics={"macro_f1": 0.86},
            config={"gold": "/path/to.jsonl"},
        )
        payload = json.loads(out.model_dump_json())
        # Top-level keys are exactly the documented six.
        assert set(payload) == {
            "eval_kind",
            "dataset",
            "git_commit",
            "ts",
            "metrics",
            "config",
        }
        assert payload["eval_kind"] == "classifier"
        assert payload["dataset"] == "ds1"
        assert payload["git_commit"] == "abc123"
        assert payload["ts"] == "2026-05-22T00:00:00Z"
        assert payload["metrics"] == {"macro_f1": 0.86}
        assert payload["config"] == {"gold": "/path/to.jsonl"}

    def test_dump_load_round_trip(self) -> None:
        before = EvalOutput(
            eval_kind="regression",
            dataset="ds2",
            metrics={"deltas": {"ndcg@10": -0.02}, "violations": []},
        )
        after = EvalOutput.model_validate_json(before.model_dump_json())
        assert after == before


class TestEvalOutputForbidExtra:
    """`extra='forbid'` keeps the envelope tight."""

    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalOutput(
                eval_kind="retrieval",
                dataset="ds",
                future_field=42,  # type: ignore[call-arg]
            )


class TestUtcNowIso:
    """The module-level timestamp helper is monkeypatch-friendly."""

    def test_returns_z_suffix_not_offset(self) -> None:
        """The runner-emitted timestamp must end with 'Z', not '+00:00'.

        Pinning the suffix shape so downstream dashboards can parse
        with a single regex instead of branching on offset / Z forms.
        """
        ts = _utc_now_iso()
        assert ts.endswith("Z"), f"timestamp lost Z suffix: {ts!r}"
        assert "+00:00" not in ts, f"timestamp leaked offset form: {ts!r}"

    def test_helper_is_independently_monkeypatchable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direct calls to ``_utc_now_iso`` can be monkeypatched.

        Note: Pydantic v2's ``default_factory`` captures the callable
        reference at class-definition time, so monkeypatching the
        module-level symbol does NOT redirect the default factory on
        already-defined models. Downstream eval modules wanting
        deterministic timestamps should construct ``EvalOutput`` with
        an explicit ``ts=...`` rather than rely on patching this
        helper. This test pins the helper-direct path (useful when
        evaluators want to emit a stable "started_at" alongside the
        envelope's "ts").
        """
        fixed = "2026-01-01T00:00:00Z"
        monkeypatch.setattr("corpus_forge.eval._schema._utc_now_iso", lambda: fixed)
        from corpus_forge.eval._schema import _utc_now_iso as patched

        assert patched() == fixed


def test_eval_kind_alias_exported() -> None:
    """The ``EvalKind`` Literal alias is part of the public surface.

    Future evaluator modules will type their signatures with
    ``EvalKind`` so the literal stays single-sourced.
    """
    # If the alias has gone, the import at the top of this file would
    # have raised — this test just pins the name as documentation.
    assert EvalKind is not None
