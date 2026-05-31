"""RED tests — `corpus_forge.embedders.routing` helper module.

Routing rule (per PR #81 design):

1. Iterate active embedders in declaration order.
2. First specialist (non-empty ``extensions``) whose allow-list matches the
   chunk's URI claims it.
3. Else the first catchall (empty ``extensions``) claims it.
4. If neither matches → ``None`` from ``route_for``; the
   ``validate_routing_invariant`` config-load gate would have caught this
   earlier, but the helper still has to defend.

The helpers operate on *Embedder-like* and *EmbedderConfig-like* shapes —
duck-typed on ``.extensions``, ``.active``, ``.name``. Tests use
``types.SimpleNamespace`` stand-ins to keep the unit isolated.
"""

from __future__ import annotations

import types

import pytest

# ──────────────────────────────────────────────────────────────────────────
# extension_for(source_uri) — single-suffix lookup
# ──────────────────────────────────────────────────────────────────────────


def test_extension_for_python() -> None:
    from corpus_forge.embedders.routing import extension_for

    assert extension_for("filesystem://vault/foo/bar.py") == ".py"


def test_extension_for_case_folded() -> None:
    from corpus_forge.embedders.routing import extension_for

    assert extension_for("FILE.PY") == ".py"
    assert extension_for("foo.TS") == ".ts"


def test_extension_for_multi_dot_returns_last() -> None:
    """Single-suffix lookup: ``.tar.gz`` returns the last segment ``.gz``.
    Multi-suffix matching happens via ``claims()``'s endswith check, not
    here."""
    from corpus_forge.embedders.routing import extension_for

    assert extension_for("foo.tar.gz") == ".gz"


def test_extension_for_no_extension_returns_empty() -> None:
    from corpus_forge.embedders.routing import extension_for

    assert extension_for("filesystem://vault/README") == ""


def test_extension_for_dotfile_returns_empty() -> None:
    """Leading-dot files (``.envrc``) are not a suffix — return empty."""
    from corpus_forge.embedders.routing import extension_for

    assert extension_for("filesystem://vault/.envrc") == ""
    assert extension_for(".gitignore") == ""


def test_extension_for_empty_string() -> None:
    from corpus_forge.embedders.routing import extension_for

    assert extension_for("") == ""


# ──────────────────────────────────────────────────────────────────────────
# claims(embedder, source_uri) — does THIS embedder's allow-list match?
# ──────────────────────────────────────────────────────────────────────────


def _make(name: str, extensions: list[str], *, active: bool = True) -> types.SimpleNamespace:
    """Embedder-shaped duck for the routing helpers (only ``.extensions``,
    ``.active``, ``.name`` are consulted)."""
    return types.SimpleNamespace(name=name, extensions=extensions, active=active)


def test_claims_catchall_matches_everything() -> None:
    from corpus_forge.embedders.routing import claims

    catchall = _make("text", [])
    assert claims(catchall, "filesystem://vault/foo.py") is True
    assert claims(catchall, "filesystem://vault/README") is True
    assert claims(catchall, "") is True


def test_claims_specialist_matches_suffix() -> None:
    from corpus_forge.embedders.routing import claims

    code = _make("code", [".py"])
    assert claims(code, "filesystem://vault/foo.py") is True


def test_claims_specialist_misses_other_suffix() -> None:
    from corpus_forge.embedders.routing import claims

    code = _make("code", [".py"])
    assert claims(code, "filesystem://vault/foo.md") is False


def test_claims_specialist_case_insensitive() -> None:
    from corpus_forge.embedders.routing import claims

    code = _make("code", [".py"])
    assert claims(code, "filesystem://vault/FOO.PY") is True


def test_claims_specialist_multi_suffix() -> None:
    """An embedder with ``.tar.gz`` claims ``foo.tar.gz`` via endswith."""
    from corpus_forge.embedders.routing import claims

    archive = _make("archive", [".tar.gz"])
    assert claims(archive, "filesystem://vault/foo.tar.gz") is True
    # And does NOT claim a plain `.gz`.
    assert claims(archive, "filesystem://vault/foo.gz") is False


# ──────────────────────────────────────────────────────────────────────────
# route_for(extension_or_uri, active_embedders) — declaration-order pick
# ──────────────────────────────────────────────────────────────────────────


def test_route_specialist_beats_catchall_when_match() -> None:
    from corpus_forge.embedders.routing import route_for

    text = _make("text", [])
    code = _make("code", [".py"])
    chosen = route_for(".py", [text, code])
    assert chosen is code


def test_route_specialist_beats_catchall_even_when_catchall_first() -> None:
    """Declaration order only matters between equal-class embedders;
    a later specialist beats an earlier catchall when the extension
    matches the specialist."""
    from corpus_forge.embedders.routing import route_for

    text = _make("text", [])
    code = _make("code", [".py"])
    chosen = route_for(".py", [text, code])
    assert chosen.name == "code"


def test_route_catchall_for_non_specialist_extension() -> None:
    from corpus_forge.embedders.routing import route_for

    text = _make("text", [])
    code = _make("code", [".py"])
    chosen = route_for(".md", [text, code])
    assert chosen is text


def test_route_two_specialists_first_wins() -> None:
    """When two specialists both match, declaration order wins."""
    from corpus_forge.embedders.routing import route_for

    a = _make("code_a", [".py"])
    b = _make("code_b", [".py"])
    chosen = route_for(".py", [a, b])
    assert chosen is a


def test_route_no_match_returns_none() -> None:
    """No catchall + the only specialist doesn't match → None."""
    from corpus_forge.embedders.routing import route_for

    code = _make("code", [".py"])
    chosen = route_for(".md", [code])
    assert chosen is None


def test_route_empty_extension_routes_to_catchall() -> None:
    from corpus_forge.embedders.routing import route_for

    text = _make("text", [])
    code = _make("code", [".py"])
    chosen = route_for("", [text, code])
    assert chosen is text


def test_route_only_specialist_extension_matches() -> None:
    """No catchall but the extension hits the specialist → specialist wins."""
    from corpus_forge.embedders.routing import route_for

    code = _make("code", [".py"])
    chosen = route_for(".py", [code])
    assert chosen is code


# ──────────────────────────────────────────────────────────────────────────
# validate_routing_invariant(embedder_configs) — config-load-time gate
# ──────────────────────────────────────────────────────────────────────────


def test_validate_invariant_specialist_without_catchall_raises() -> None:
    from corpus_forge.embedders.routing import (
        EmbedderRoutingError,
        validate_routing_invariant,
    )

    code = _make("code", [".py"], active=True)
    with pytest.raises(EmbedderRoutingError) as exc_info:
        validate_routing_invariant([code])
    assert "catchall" in str(exc_info.value).lower()


def test_validate_invariant_catchall_plus_specialist_ok() -> None:
    from corpus_forge.embedders.routing import validate_routing_invariant

    text = _make("text", [], active=True)
    code = _make("code", [".py"], active=True)
    # No exception.
    validate_routing_invariant([text, code])


def test_validate_invariant_only_catchalls_ok() -> None:
    """Today's single-tower configs (every embedder is a catchall) must
    keep validating after this change ships."""
    from corpus_forge.embedders.routing import validate_routing_invariant

    a = _make("a", [], active=True)
    b = _make("b", [], active=True)
    validate_routing_invariant([a, b])


def test_validate_invariant_inactive_specialist_ignored() -> None:
    """An inactive specialist isn't in play — the rule doesn't have to
    cover it, even without an active catchall."""
    from corpus_forge.embedders.routing import validate_routing_invariant

    code = _make("code", [".py"], active=False)
    validate_routing_invariant([code])


def test_validate_invariant_empty_list_ok() -> None:
    from corpus_forge.embedders.routing import validate_routing_invariant

    validate_routing_invariant([])


def test_embedder_routing_error_is_valueerror() -> None:
    """``EmbedderRoutingError`` must subclass ``ValueError`` so the
    Pydantic ``@model_validator`` wrapping picks it up as a validation
    failure (rather than an unrelated ``RuntimeError``)."""
    from corpus_forge.embedders.routing import EmbedderRoutingError

    assert issubclass(EmbedderRoutingError, ValueError)
