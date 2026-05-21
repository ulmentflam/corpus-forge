"""Integration-light tests pinning the chat-source wiring in ``ingest.py``.

For each new plugin name (``gemini_cli``, ``codex_cli``, ``chatgpt_export``,
``jsonl_chat``) we assert that ``_instantiate_source`` returns the right
concrete class when the matching config field is set, and raises a
``ValueError`` with a useful hint when it isn't.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.config import DatasetSourceConfig
from corpus_forge.ingest import _SOURCE_URI_TO_CLIENT, _instantiate_source
from corpus_forge.sources.chatgpt_export import ChatGPTExportSource
from corpus_forge.sources.codex_cli import CodexCLISource
from corpus_forge.sources.gemini_cli import GeminiCLISource
from corpus_forge.sources.jsonl_chat import JSONLChatSource

pytestmark = pytest.mark.unit


def _make(plugin: str, **fields) -> DatasetSourceConfig:
    fields.setdefault("chunker", "conversation")
    # ExpandedPath fields take strings; coerce any Path values so tests
    # can write tmp_path naturally.
    coerced = {k: (str(v) if isinstance(v, Path) else v) for k, v in fields.items()}
    return DatasetSourceConfig(plugin=plugin, **coerced)


def test_gemini_cli_instantiated(tmp_path: Path) -> None:
    src = _instantiate_source(_make("gemini_cli", chats_root=tmp_path))
    assert isinstance(src, GeminiCLISource)


def test_codex_cli_instantiated(tmp_path: Path) -> None:
    src = _instantiate_source(_make("codex_cli", sessions_root=tmp_path))
    assert isinstance(src, CodexCLISource)


def test_chatgpt_export_instantiated(tmp_path: Path) -> None:
    src = _instantiate_source(_make("chatgpt_export", export_root=tmp_path))
    assert isinstance(src, ChatGPTExportSource)


def test_jsonl_chat_instantiated(tmp_path: Path) -> None:
    src = _instantiate_source(_make("jsonl_chat", path=tmp_path))
    assert isinstance(src, JSONLChatSource)


@pytest.mark.parametrize(
    ("plugin", "field"),
    [
        ("gemini_cli", "chats_root"),
        ("codex_cli", "sessions_root"),
        ("chatgpt_export", "export_root"),
        ("jsonl_chat", "path"),
    ],
)
def test_missing_path_field_raises(plugin: str, field: str) -> None:
    cfg = _make(plugin)
    with pytest.raises(ValueError, match=field):
        _instantiate_source(cfg)


@pytest.mark.parametrize(
    ("plugin", "field", "blank"),
    [
        ("gemini_cli", "chats_root", ""),
        ("gemini_cli", "chats_root", "   "),
        ("codex_cli", "sessions_root", ""),
        ("chatgpt_export", "export_root", "  "),
        ("jsonl_chat", "path", ""),
    ],
)
def test_blank_path_field_raises(plugin: str, field: str, blank: str) -> None:
    """Empty / whitespace-only path values must raise the same hint as None
    so we don't silently fall back to ``Path("")`` (which resolves to CWD).
    """
    cfg = _make(plugin, **{field: blank})
    with pytest.raises(ValueError, match=field):
        _instantiate_source(cfg)


def test_uri_to_client_table_includes_new_clients() -> None:
    assert _SOURCE_URI_TO_CLIENT["codex-cli://"] == "codex-cli"
    assert _SOURCE_URI_TO_CLIENT["chatgpt-export://"] == "chatgpt-export"
    assert _SOURCE_URI_TO_CLIENT["jsonl-chat://"] == "jsonl-chat"
    # claude-code-history shares the claude-code feedback-sessions client.
    assert _SOURCE_URI_TO_CLIENT["claude-code-history://"] == "claude-code"
