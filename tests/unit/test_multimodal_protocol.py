"""Phase G (G-10) — :class:`MultiModalEmbedder` Protocol + exceptions."""

from __future__ import annotations

import pytest

from corpus_forge.embedders.multimodal import (
    MultiModalEmbedder,
    MultiModalEmbedderError,
    MultiModalResponseError,
    MultiModalTimeoutError,
    MultiModalUnavailableError,
)

# ── Protocol surface ────────────────────────────────────────────────────


def test_protocol_is_runtime_checkable() -> None:
    assert getattr(MultiModalEmbedder, "_is_runtime_protocol", False) is True


def test_protocol_isinstance_stub() -> None:
    class _Stub:
        name = "stub"
        dimension = 8

        def encode_text(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 8 for _ in texts]

        def encode_image(self, images: list[bytes]) -> list[list[float]]:
            return [[0.0] * 8 for _ in images]

        def warmup(self) -> None:
            return None

    assert isinstance(_Stub(), MultiModalEmbedder)


def test_protocol_rejects_missing_method() -> None:
    class _Incomplete:
        name = "incomplete"
        dimension = 8

        def encode_text(self, texts: list[str]) -> list[list[float]]:
            return []

        def warmup(self) -> None:
            return None

    assert not isinstance(_Incomplete(), MultiModalEmbedder)


# ── Exception hierarchy ─────────────────────────────────────────────────


class TestExceptionHierarchy:
    def test_unavailable_is_base(self) -> None:
        assert issubclass(MultiModalUnavailableError, MultiModalEmbedderError)

    def test_timeout_is_base(self) -> None:
        assert issubclass(MultiModalTimeoutError, MultiModalEmbedderError)

    def test_response_is_base(self) -> None:
        assert issubclass(MultiModalResponseError, MultiModalEmbedderError)

    def test_base_is_exception(self) -> None:
        assert issubclass(MultiModalEmbedderError, Exception)

    def test_distinct_classes(self) -> None:
        assert MultiModalUnavailableError is not MultiModalTimeoutError
        assert MultiModalTimeoutError is not MultiModalResponseError
        assert MultiModalResponseError is not MultiModalUnavailableError


def test_raises_propagate() -> None:
    """Sanity: each exception can be raised and caught as the base."""
    for cls in (MultiModalUnavailableError, MultiModalTimeoutError, MultiModalResponseError):
        with pytest.raises(MultiModalEmbedderError):
            raise cls("test")
