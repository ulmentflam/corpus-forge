"""Phase I-11 — daily PyPI version-check ping."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from corpus_forge.update.version_check import (
    CACHE_TTL_S,
    VersionCheckResult,
    check_for_update,
)


def _write_cache(path: Path, *, latest: str, when_unix: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"latest": latest, "last_checked_unix": when_unix}),
        encoding="utf-8",
    )


class TestOptOut:
    def test_env_var_returns_none(self, tmp_path: Path) -> None:
        result = check_for_update(
            cache_path=tmp_path / "version.json",
            env={"CF_NO_VERSION_CHECK": "1"},
        )
        assert result is None


class TestCache:
    def test_cache_hit_uses_cached_latest(self, tmp_path: Path) -> None:
        cache = tmp_path / "version.json"
        _write_cache(cache, latest="9.9.9", when_unix=1_000_000)
        result = check_for_update(
            cache_path=cache,
            now=1_000_000 + 1.0,  # well under CACHE_TTL_S
            env={},
            installed="0.1.0b1",
        )
        assert isinstance(result, VersionCheckResult)
        assert result.served_from_cache
        assert result.latest == "9.9.9"
        assert result.is_newer_available

    def test_cache_miss_pings_pypi(self, tmp_path: Path) -> None:
        cache = tmp_path / "version.json"
        with patch(
            "corpus_forge.update.version_check._fetch_latest",
            return_value="2.0.0",
        ):
            result = check_for_update(
                cache_path=cache,
                now=1.0,
                env={},
                installed="0.1.0b1",
            )
        assert result is not None
        assert not result.served_from_cache
        assert result.latest == "2.0.0"
        assert cache.exists()

    def test_stale_cache_triggers_fetch(self, tmp_path: Path) -> None:
        cache = tmp_path / "version.json"
        _write_cache(cache, latest="1.0.0", when_unix=0)
        with patch(
            "corpus_forge.update.version_check._fetch_latest",
            return_value="3.0.0",
        ) as fetch:
            check_for_update(
                cache_path=cache,
                now=CACHE_TTL_S + 1.0,
                env={},
                installed="0.1.0b1",
            )
        fetch.assert_called_once()

    def test_fetch_failure_falls_back_to_cached(self, tmp_path: Path) -> None:
        cache = tmp_path / "version.json"
        _write_cache(cache, latest="1.0.0", when_unix=0)
        with patch(
            "corpus_forge.update.version_check._fetch_latest",
            return_value=None,
        ):
            result = check_for_update(
                cache_path=cache,
                now=CACHE_TTL_S + 1.0,
                env={},
                installed="0.1.0b1",
            )
        assert result is not None
        # No new fetch result — fall back to whatever the stale cache held.
        assert result.latest == "1.0.0"


class TestNotice:
    def test_newer_emits_notice(self) -> None:
        r = VersionCheckResult(
            installed="0.1.0b1",
            latest="0.2.0",
            is_newer_available=True,
            served_from_cache=False,
            cache_path=Path("/tmp/x"),
        )
        notice = r.notice()
        assert notice is not None
        assert "0.2.0" in notice
        assert "corpus-forge update" in notice

    def test_same_emits_none(self) -> None:
        r = VersionCheckResult(
            installed="0.2.0",
            latest="0.2.0",
            is_newer_available=False,
            served_from_cache=True,
            cache_path=Path("/tmp/x"),
        )
        assert r.notice() is None
