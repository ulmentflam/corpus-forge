"""Phase M Wave 4 — ``_check_zotero`` doctor check.

Status logic:

  - ``SKIP`` when no source has ``plugin == "zotero"``.
  - ``OK``   when local-mode path exists and opens read-only.
  - ``FAIL`` when local-mode path is missing.
  - ``WARN`` when web-mode API key env var is unset.
  - ``WARN`` when ``both``-mode local path is missing but web is configured.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from corpus_forge.doctor.checks import CheckStatus, _check_zotero

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "zotero"


def _cfg_with_zotero_source(**zotero_overrides: object):
    """Build a tiny Config-like stub that exposes ``datasets[].sources[]``."""
    src = MagicMock()
    src.plugin = "zotero"
    # Defaults the check should read.
    z = MagicMock()
    z.mode = zotero_overrides.get("mode", "local")
    z.library_path = zotero_overrides.get("library_path")
    z.user_id = zotero_overrides.get("user_id")
    z.api_key_env = zotero_overrides.get("api_key_env", "ZOTERO_API_KEY")
    z.library_type = zotero_overrides.get("library_type", "user")
    z.group_id = zotero_overrides.get("group_id")
    src.zotero = z
    ds = MagicMock()
    ds.sources = [src]
    cfg = MagicMock()
    cfg.datasets = [ds]
    return cfg


def _cfg_without_zotero():
    src = MagicMock()
    src.plugin = "filesystem"
    src.zotero = None
    ds = MagicMock()
    ds.sources = [src]
    cfg = MagicMock()
    cfg.datasets = [ds]
    return cfg


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    target = tmp_path / "zotero"
    shutil.copytree(FIXTURE_DIR, target)
    return target


class TestSkip:
    def test_skip_when_no_zotero_source(self) -> None:
        result = _check_zotero(_cfg_without_zotero())
        assert result.status == CheckStatus.SKIP


class TestLocalMode:
    def test_ok_when_local_path_opens(self, fixture_dir: Path) -> None:
        cfg = _cfg_with_zotero_source(mode="local", library_path=str(fixture_dir / "zotero.sqlite"))
        result = _check_zotero(cfg)
        assert result.status == CheckStatus.OK

    def test_fail_when_local_path_missing(self, tmp_path: Path) -> None:
        cfg = _cfg_with_zotero_source(mode="local", library_path=str(tmp_path / "missing.sqlite"))
        result = _check_zotero(cfg)
        assert result.status == CheckStatus.FAIL


class TestWebMode:
    def test_warn_when_api_key_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
        cfg = _cfg_with_zotero_source(mode="web", user_id="123")
        result = _check_zotero(cfg)
        assert result.status == CheckStatus.WARN

    def test_ok_when_api_key_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ZOTERO_API_KEY", "fake-key")
        cfg = _cfg_with_zotero_source(mode="web", user_id="123")
        result = _check_zotero(cfg)
        assert result.status == CheckStatus.OK


class TestBothMode:
    def test_warn_when_local_missing_in_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ZOTERO_API_KEY", "fake-key")
        cfg = _cfg_with_zotero_source(
            mode="both",
            library_path=str(tmp_path / "missing.sqlite"),
            user_id="123",
        )
        result = _check_zotero(cfg)
        # Should NOT fail outright — web path still works.
        assert result.status == CheckStatus.WARN
