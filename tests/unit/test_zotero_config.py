"""Phase M Wave 4 — ``ZoteroSourceConfig`` validators.

The block lives at ``corpus_forge.config.ZoteroSourceConfig`` and is
attached as a nested ``zotero`` field on ``DatasetSourceConfig``.

Mode-conditional rules:

  - ``mode == "local"`` allows missing ``user_id`` / ``group_id``.
  - ``mode == "web"`` requires ``user_id``.
  - ``mode == "both"`` requires ``user_id`` (local path auto-resolves).
  - ``library_type == "group"`` requires ``group_id`` (regardless of mode).
  - Unknown ``mode`` is rejected by the ``Literal`` constraint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from corpus_forge.config import ZoteroSourceConfig


class TestZoteroSourceConfigDefaults:
    def test_default_mode_is_local(self) -> None:
        cfg = ZoteroSourceConfig()
        assert cfg.mode == "local"
        assert cfg.library_path is None
        assert cfg.user_id is None
        assert cfg.api_key_env == "ZOTERO_API_KEY"
        assert cfg.library_type == "user"
        assert cfg.group_id is None
        assert cfg.include_attachments == ["application/pdf"]
        assert cfg.include_collections == []
        assert cfg.exclude_collections == []

    def test_base_url_default(self) -> None:
        cfg = ZoteroSourceConfig()
        assert str(cfg.base_url).startswith("https://api.zotero.org")


class TestModeValidators:
    def test_local_mode_allows_no_credentials(self) -> None:
        cfg = ZoteroSourceConfig(mode="local", library_path="/tmp/zotero")
        assert cfg.mode == "local"
        # ``ExpandedPath`` normalises to native form, so on Windows the
        # stored value becomes ``\tmp\zotero``. Compare canonical POSIX.
        assert Path(str(cfg.library_path)).as_posix().endswith("/tmp/zotero")
        assert cfg.user_id is None

    def test_web_mode_requires_user_id(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ZoteroSourceConfig(mode="web")
        assert "user_id" in str(ei.value)

    def test_web_mode_with_user_id_validates(self) -> None:
        cfg = ZoteroSourceConfig(mode="web", user_id="123456")
        assert cfg.user_id == "123456"

    def test_both_mode_requires_user_id(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ZoteroSourceConfig(mode="both")
        assert "user_id" in str(ei.value)

    def test_both_mode_with_user_id_validates(self) -> None:
        cfg = ZoteroSourceConfig(mode="both", user_id="123456")
        assert cfg.mode == "both"

    def test_group_library_type_requires_group_id(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ZoteroSourceConfig(mode="web", user_id="123456", library_type="group")
        assert "group_id" in str(ei.value)

    def test_group_library_type_with_group_id_validates(self) -> None:
        cfg = ZoteroSourceConfig(mode="web", user_id="123", library_type="group", group_id="999")
        assert cfg.library_type == "group"
        assert cfg.group_id == "999"

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ZoteroSourceConfig(mode="unknown-mode")  # type: ignore[arg-type]


class TestEnvVarName:
    def test_api_key_env_must_be_posix_identifier(self) -> None:
        # The same env-var-name discipline used by VLMConfig / WhisperConfig
        # applies here too — empty or space-containing names are rejected.
        with pytest.raises(ValidationError):
            ZoteroSourceConfig(api_key_env="MY KEY")

    def test_valid_env_var_name_accepted(self) -> None:
        cfg = ZoteroSourceConfig(api_key_env="MY_ZOTERO_KEY")
        assert cfg.api_key_env == "MY_ZOTERO_KEY"
