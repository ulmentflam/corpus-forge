"""Pure-filesystem checks for the realistic code-sample fixtures.

The ``code/realistic/<lang>/inventory.*`` modules under the multi-format
fixture corpus are richer, idiomatic, multi-construct sources (one each
for python / typescript / go / rust) that give the code-embedder lane
something heavier than the hello-world ``code/<lang>/`` stubs to rank
against. They are emitted deterministically by
``scripts/build_fixture_corpus.py`` (``build_realistic_code``).

These tests touch only the filesystem — no model, no DB — so they run in
the fast unit lane. They guard that the committed bytes exist, are
non-trivial, decode as UTF-8, and carry each language's signature
construct (so a regen that silently drops content is caught here rather
than in the slow Postgres e2e).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"
_REALISTIC_ROOT = _FIXTURE_ROOT / "code" / "realistic"

# Minimum size that distinguishes a richer module from a hello-world stub.
_MIN_BYTES = 400

# (relative path, signature substrings that must ALL be present).
_FILES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python/inventory.py", ("def ", "class ")),
    ("typescript/inventory.ts", ("interface ", "class ")),
    ("go/inventory.go", ("func ", "struct")),
    ("rust/inventory.rs", ("fn ", "struct")),
)


@pytest.mark.parametrize(("rel_path", "signatures"), _FILES)
def test_realistic_fixture_present_and_rich(rel_path: str, signatures: tuple[str, ...]) -> None:
    """Each realistic module exists, is non-trivial, UTF-8, and idiomatic."""
    path = _REALISTIC_ROOT / rel_path
    assert path.is_file(), (
        f"Missing realistic code fixture: {path}. Re-run "
        "`uv run python scripts/build_fixture_corpus.py`."
    )

    raw = path.read_bytes()
    assert len(raw) > _MIN_BYTES, (
        f"{rel_path} is only {len(raw)} bytes; expected > {_MIN_BYTES} "
        "(should be a richer module, not a stub)."
    )

    text = raw.decode("utf-8")  # raises UnicodeDecodeError if not valid UTF-8
    for signature in signatures:
        assert signature in text, f"{rel_path} is missing signature construct {signature!r}"
