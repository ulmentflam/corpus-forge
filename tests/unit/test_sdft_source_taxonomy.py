"""Q2-T1 RED — Unit tests for the SDFTSource enum taxonomy.

Verifies that ``corpus_forge.sdft.sources.SDFTSource``:
  1. Is importable.
  2. Has exactly 8 values covering all required source names.
  3. Includes all four capture-event sources and all four chat-client sources.
  4. Has an ``is_chat_client(source)`` class/static method that returns
     ``True`` for the four chat-client values and ``False`` for the others.
  5. Round-trips cleanly through string conversion (``StrEnum`` contract).

RED state
---------
``SDFTSource.is_chat_client`` does not exist yet.  Tests that call it will
fail with ``AttributeError: type object 'SDFTSource' has no attribute
'is_chat_client'``.

The import itself (``from corpus_forge.sdft.sources import SDFTSource``) was
shipped in Q1-G1 and should succeed.  The ``is_chat_client`` method is the
new surface added in Q2.

Run command::

    uv run pytest tests/unit/test_sdft_source_taxonomy.py -v 2>&1 | tail -40
"""

from __future__ import annotations

import enum

import pytest

# ---------------------------------------------------------------------------
# Expected values
# ---------------------------------------------------------------------------

_EXPECTED_CAPTURE_SOURCES = {
    "curation_commit",
    "rate_search_result",
    "record_demonstration",
    "cli_feedback",
}

_EXPECTED_CHAT_CLIENT_SOURCES = {
    "claude_code",
    "gemini",
    "opencode",
    "codex",
}

_ALL_EXPECTED = _EXPECTED_CAPTURE_SOURCES | _EXPECTED_CHAT_CLIENT_SOURCES


# ===========================================================================
# 1. Import smoke
# ===========================================================================


class TestSDFTSourceImport:
    """SDFTSource must be importable from corpus_forge.sdft.sources."""

    def test_sdft_source_importable(self) -> None:
        """``from corpus_forge.sdft.sources import SDFTSource`` must not raise."""
        from corpus_forge.sdft.sources import SDFTSource

        assert SDFTSource is not None

    def test_sdft_source_is_str_enum(self) -> None:
        """SDFTSource must be a StrEnum (values are plain strings)."""
        from corpus_forge.sdft.sources import SDFTSource

        assert issubclass(SDFTSource, str), "SDFTSource must subclass str (StrEnum)"
        assert issubclass(SDFTSource, enum.Enum), "SDFTSource must subclass Enum"


# ===========================================================================
# 2. Exact cardinality — must have exactly 8 values
# ===========================================================================


class TestSDFTSourceCardinality:
    """SDFTSource must have exactly 8 members."""

    def test_enum_has_exactly_eight_values(self) -> None:
        """SDFTSource must define exactly 8 members — no more, no fewer."""
        from corpus_forge.sdft.sources import SDFTSource

        enum_values = {e.value for e in SDFTSource}
        assert len(enum_values) == 8, (
            f"Expected 8 SDFTSource values; got {len(enum_values)}: {sorted(enum_values)}"
        )

    def test_enum_covers_all_expected_values(self) -> None:
        """SDFTSource enum must include all 8 required source names."""
        from corpus_forge.sdft.sources import SDFTSource

        enum_values = {e.value for e in SDFTSource}
        missing = _ALL_EXPECTED - enum_values
        assert not missing, f"SDFTSource is missing required values: {sorted(missing)}"

    def test_enum_has_no_extra_unexpected_values(self) -> None:
        """SDFTSource must not define any source name outside the expected set."""
        from corpus_forge.sdft.sources import SDFTSource

        enum_values = {e.value for e in SDFTSource}
        extra = enum_values - _ALL_EXPECTED
        assert not extra, f"SDFTSource has unexpected extra values: {sorted(extra)}"


# ===========================================================================
# 3. Value membership — specific names must exist
# ===========================================================================


class TestSDFTSourceMembership:
    """Each of the 8 required values must be accessible on the enum."""

    @pytest.mark.parametrize("value", sorted(_ALL_EXPECTED))
    def test_expected_value_present(self, value: str) -> None:
        """SDFTSource must have a member with value ``{value}``."""
        from corpus_forge.sdft.sources import SDFTSource

        enum_values = {e.value for e in SDFTSource}
        assert value in enum_values, (
            f"SDFTSource is missing required value {value!r}; current values: {sorted(enum_values)}"
        )


# ===========================================================================
# 4. is_chat_client() — the new Q2 surface
# ===========================================================================


class TestIsChatClient:
    """SDFTSource.is_chat_client(source) returns True for chat-client sources only."""

    @pytest.mark.parametrize("source", sorted(_EXPECTED_CHAT_CLIENT_SOURCES))
    def test_chat_client_sources_return_true(self, source: str) -> None:
        """``SDFTSource.is_chat_client({source!r})`` must return True."""
        from corpus_forge.sdft.sources import SDFTSource

        result = SDFTSource.is_chat_client(source)
        assert result is True, (
            f"Expected SDFTSource.is_chat_client({source!r}) == True; got {result!r}"
        )

    @pytest.mark.parametrize("source", sorted(_EXPECTED_CAPTURE_SOURCES))
    def test_capture_sources_return_false(self, source: str) -> None:
        """``SDFTSource.is_chat_client({source!r})`` must return False."""
        from corpus_forge.sdft.sources import SDFTSource

        result = SDFTSource.is_chat_client(source)
        assert result is False, (
            f"Expected SDFTSource.is_chat_client({source!r}) == False; got {result!r}"
        )

    def test_is_chat_client_accepts_string(self) -> None:
        """is_chat_client must accept a plain str, not just an enum member."""
        from corpus_forge.sdft.sources import SDFTSource

        # Pass a raw string — must not raise.
        result = SDFTSource.is_chat_client("claude_code")
        assert result is True

    def test_is_chat_client_accepts_enum_member(self) -> None:
        """is_chat_client must accept an SDFTSource enum member directly."""
        from corpus_forge.sdft.sources import SDFTSource

        result = SDFTSource.is_chat_client(SDFTSource.CLAUDE_CODE)
        assert result is True

    def test_is_chat_client_unknown_value_returns_false(self) -> None:
        """is_chat_client with an unrecognised value must return False, not raise."""
        from corpus_forge.sdft.sources import SDFTSource

        result = SDFTSource.is_chat_client("not_a_real_source_xyz")
        assert result is False, f"Expected False for unknown source; got {result!r}"


# ===========================================================================
# 5. StrEnum round-trip
# ===========================================================================


class TestSDFTSourceStringRoundTrip:
    """SDFTSource values are plain strings (StrEnum contract)."""

    @pytest.mark.parametrize("value", sorted(_ALL_EXPECTED))
    def test_str_round_trip(self, value: str) -> None:
        """``str(SDFTSource(value))`` must equal ``value``."""
        from corpus_forge.sdft.sources import SDFTSource

        member = SDFTSource(value)
        assert str(member) == value, (
            f"str(SDFTSource({value!r})) == {str(member)!r}; expected {value!r}"
        )

    def test_enum_value_equals_string(self) -> None:
        """``SDFTSource.CLAUDE_CODE == 'claude_code'`` must be True (StrEnum)."""
        from corpus_forge.sdft.sources import SDFTSource

        assert SDFTSource.CLAUDE_CODE == "claude_code", (
            f"Expected SDFTSource.CLAUDE_CODE == 'claude_code'; got {SDFTSource.CLAUDE_CODE!r}"
        )
