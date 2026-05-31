"""RED tests — `EmbedderConfig.extensions` field + `Config`-level routing invariant.

Feature: extension-based chunk routing for dual-tower embedder setups
(PR #81). The Pydantic schema in ``corpus_forge.config.EmbedderConfig`` gains
an ``extensions: list[str]`` field that, when non-empty, marks the embedder
as a *specialist* over those file extensions. Validation:

- Leading dot required (``"py"`` rejected, ``".py"`` accepted).
- Empty strings rejected.
- Case-folded to lowercase on the way in (``".PY"`` → ``".py"``).
- Multi-dot extensions like ``".tar.gz"`` accepted — the routing matcher
  uses ``endswith`` semantics rather than a single-suffix lookup.

Cross-cutting ``Config`` invariant: when one or more *active* specialists
exist, there MUST be at least one active catchall (an embedder with an
empty ``extensions`` list) OR the config load fails — this is the only way
to guarantee every chunk has a home. Inactive specialists don't trigger
the invariant.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ──────────────────────────────────────────────────────────────────────────
# EmbedderConfig.extensions field — defaults + round-trip
# ──────────────────────────────────────────────────────────────────────────


def test_extensions_default_empty() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="nomic",
        provider="sentence_transformers",
        model_id="nomic-embed-text-v1.5",
        dimension=768,
    )
    assert cfg.extensions == []


def test_extensions_round_trip_simple() -> None:
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="code",
        provider="sentence_transformers",
        model_id="nomic-embed-code",
        dimension=3584,
        extensions=[".py", ".ts"],
    )
    assert cfg.extensions == [".py", ".ts"]


def test_extensions_lowercased() -> None:
    """Mixed-case input is normalised to lowercase."""
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="code",
        provider="sentence_transformers",
        model_id="nomic-embed-code",
        dimension=3584,
        extensions=[".PY", ".Ts"],
    )
    assert cfg.extensions == [".py", ".ts"]


def test_extensions_missing_leading_dot_rejected() -> None:
    """Bare ``"py"`` (no leading dot) is a clear user error — reject with the
    offending value named in the message."""
    from corpus_forge.config import EmbedderConfig

    with pytest.raises(ValidationError) as exc_info:
        EmbedderConfig(
            name="code",
            provider="sentence_transformers",
            model_id="x",
            dimension=128,
            extensions=["py"],
        )
    msg = str(exc_info.value)
    assert "py" in msg
    assert "dot" in msg.lower() or "." in msg


def test_extensions_empty_string_rejected() -> None:
    from corpus_forge.config import EmbedderConfig

    with pytest.raises(ValidationError):
        EmbedderConfig(
            name="code",
            provider="sentence_transformers",
            model_id="x",
            dimension=128,
            extensions=[""],
        )


def test_extensions_multi_suffix_accepted() -> None:
    """``.tar.gz``-style multi-suffix entries are valid — routing uses
    endswith semantics so the multi-dot form still matches ``foo.tar.gz``.
    """
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="archive",
        provider="sentence_transformers",
        model_id="x",
        dimension=128,
        extensions=[".tar.gz"],
    )
    assert cfg.extensions == [".tar.gz"]


def test_extensions_preserved_order() -> None:
    """Order is preserved so first-match-wins is deterministic when an
    embedder lists overlapping suffixes."""
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="code",
        provider="sentence_transformers",
        model_id="x",
        dimension=128,
        extensions=[".ts", ".tsx", ".js"],
    )
    assert cfg.extensions == [".ts", ".tsx", ".js"]


# ──────────────────────────────────────────────────────────────────────────
# Config-level invariant — `validate_routing_invariant`
# ──────────────────────────────────────────────────────────────────────────


def _minimal_config_kwargs(embedders: list[dict]) -> dict:
    """Build minimal valid Config kwargs with a SQLite backend + one dataset.

    Centralised here so each invariant test below only declares its embedder
    list; the rest of the config stays identical and irrelevant.
    """
    return {
        "backend": {"kind": "sqlite", "dsn": "/tmp/cf-test.db", "schema": "corpus"},
        "daemon": {},
        "datasets": [
            {
                "name": "default",
                "kind": "text",
                "description": "test",
                "sources": [
                    {
                        "name": "vault",
                        "plugin": "filesystem",
                        "chunker": "markdown",
                        "filesystem": {"root": "/tmp"},
                    }
                ],
            }
        ],
        "embedders": embedders,
    }


def test_config_invariant_specialist_without_catchall_rejected() -> None:
    """Active specialist + NO active catchall → fail at config-load time."""
    from corpus_forge.config import Config

    with pytest.raises(ValidationError) as exc_info:
        Config.model_validate(
            _minimal_config_kwargs(
                [
                    {
                        "name": "code",
                        "provider": "sentence_transformers",
                        "model_id": "nomic-embed-code",
                        "dimension": 3584,
                        "active": True,
                        "extensions": [".py"],
                    },
                ]
            )
        )
    msg = str(exc_info.value)
    assert "catchall" in msg.lower() or "EmbedderRoutingError" in msg


def test_config_invariant_specialist_plus_catchall_ok() -> None:
    from corpus_forge.config import Config

    # No exception — the catchall covers everything the specialist doesn't.
    cfg = Config.model_validate(
        _minimal_config_kwargs(
            [
                {
                    "name": "text",
                    "provider": "sentence_transformers",
                    "model_id": "nomic-embed-text-v1.5",
                    "dimension": 768,
                    "active": True,
                },
                {
                    "name": "code",
                    "provider": "sentence_transformers",
                    "model_id": "nomic-embed-code",
                    "dimension": 3584,
                    "active": True,
                    "extensions": [".py", ".ts"],
                },
            ]
        )
    )
    assert {e.name for e in cfg.embedders} == {"text", "code"}


def test_config_invariant_no_specialists_ok() -> None:
    """Single-tower setup (no extensions on any embedder) — today's behaviour
    must continue to validate."""
    from corpus_forge.config import Config

    cfg = Config.model_validate(
        _minimal_config_kwargs(
            [
                {
                    "name": "text",
                    "provider": "sentence_transformers",
                    "model_id": "nomic-embed-text-v1.5",
                    "dimension": 768,
                    "active": True,
                },
            ]
        )
    )
    assert cfg.embedders[0].extensions == []


def test_config_invariant_inactive_specialist_not_triggered() -> None:
    """An *inactive* specialist must not gate the catchall requirement —
    the embedder isn't in play, so routing doesn't have to cover it."""
    from corpus_forge.config import Config

    cfg = Config.model_validate(
        _minimal_config_kwargs(
            [
                {
                    "name": "code",
                    "provider": "sentence_transformers",
                    "model_id": "nomic-embed-code",
                    "dimension": 3584,
                    "active": False,
                    "extensions": [".py"],
                },
            ]
        )
    )
    # Should validate; only the inactive specialist is declared and the rule
    # ignores inactive embedders.
    assert cfg.embedders[0].active is False
