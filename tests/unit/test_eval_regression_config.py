"""Unit tests for ``EvalRegressionConfig``.

RFC ``rfc-eval-framework-expansion`` (P1) foundation task: the
configuration block that backs the future ``corpus-forge eval
regression --baseline`` verb's per-metric tolerance gating.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from corpus_forge.config import EvalRegressionConfig


class TestDefaults:
    """Default-constructed config is enabled with a 2 pp band."""

    def test_no_args(self) -> None:
        cfg = EvalRegressionConfig()
        assert cfg.enabled is True
        assert cfg.default_tolerance == 0.02
        assert cfg.per_metric == {}


class TestDefaultToleranceBounds:
    """`default_tolerance` is bounded `[0.0, 1.0]`."""

    def test_zero_accepted(self) -> None:
        assert EvalRegressionConfig(default_tolerance=0.0).default_tolerance == 0.0

    def test_one_accepted(self) -> None:
        assert EvalRegressionConfig(default_tolerance=1.0).default_tolerance == 1.0

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalRegressionConfig(default_tolerance=-0.01)

    def test_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalRegressionConfig(default_tolerance=1.01)


class TestPerMetric:
    """`per_metric` is a metric-name → tolerance dict; each value bounded."""

    def test_empty_default(self) -> None:
        assert EvalRegressionConfig().per_metric == {}

    def test_populated_passes(self) -> None:
        cfg = EvalRegressionConfig(
            per_metric={"ndcg@10": 0.01, "macro_f1": 0.03, "mae.clarity": 0.5},
        )
        assert cfg.per_metric == {
            "ndcg@10": 0.01,
            "macro_f1": 0.03,
            "mae.clarity": 0.5,
        }

    def test_out_of_band_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalRegressionConfig(per_metric={"ndcg@10": 1.5})

    def test_negative_value_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalRegressionConfig(per_metric={"ndcg@10": -0.1})

    def test_validation_error_names_offending_metric(self) -> None:
        """The error surfaces the bad metric so users see which entry failed."""
        with pytest.raises(ValidationError) as exc_info:
            EvalRegressionConfig(per_metric={"good": 0.1, "bad": 2.0})
        assert "bad" in str(exc_info.value)


class TestToleranceForLookup:
    """`tolerance_for(name)` returns per_metric override or default."""

    def test_uses_default_when_metric_absent(self) -> None:
        cfg = EvalRegressionConfig(default_tolerance=0.05)
        assert cfg.tolerance_for("ndcg@10") == 0.05

    def test_uses_override_when_present(self) -> None:
        cfg = EvalRegressionConfig(
            default_tolerance=0.05,
            per_metric={"ndcg@10": 0.01},
        )
        assert cfg.tolerance_for("ndcg@10") == 0.01
        # A different metric falls back to default.
        assert cfg.tolerance_for("macro_f1") == 0.05


class TestEnabledFlag:
    """`enabled=False` lets users record-without-fail."""

    def test_default_true(self) -> None:
        assert EvalRegressionConfig().enabled is True

    def test_can_be_false(self) -> None:
        cfg = EvalRegressionConfig(enabled=False)
        assert cfg.enabled is False


class TestExtraForbid:
    """`extra='forbid'` catches typos in `[eval_regression]` blocks."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvalRegressionConfig(
                default_tolerance=0.02,
                future_field=42,  # type: ignore[call-arg]
            )


class TestWiredOntoConfig:
    """The block is registered on the top-level Config schema."""

    def test_config_schema_includes_eval_regression(self) -> None:
        """Pydantic's model_fields exposes the field — proves the wiring.

        Avoids instantiating a full ``Config`` (which has required
        nested fields the regression-config test shouldn't have to
        know about); the schema introspection is sufficient.
        """
        from corpus_forge.config import Config

        assert "eval_regression" in Config.model_fields
        field = Config.model_fields["eval_regression"]
        assert field.annotation is EvalRegressionConfig
