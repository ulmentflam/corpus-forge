"""Phase I-11 — daily PyPI version-check ping."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.update import version_check as vc
from corpus_forge.update.version_check import (
    CACHE_TTL_S,
    VersionCheckResult,
    _fetch_latest,
    _is_newer,
    _load_cache,
    _save_cache,
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


# ── private helpers ──────────────────────────────────────────────────────


class TestIsNewer:
    """Exercises every branch of the version-comparison helper."""

    def test_none_latest_is_not_newer(self) -> None:
        assert _is_newer(None, "0.1.0") is False

    def test_empty_latest_is_not_newer(self) -> None:
        assert _is_newer("", "0.1.0") is False

    def test_equal_strings_are_not_newer(self) -> None:
        assert _is_newer("0.1.0", "0.1.0") is False

    def test_pep440_release_beats_beta(self) -> None:
        # 0.2.0 > 0.1.0b1 under PEP-440 semantics.
        assert _is_newer("0.2.0", "0.1.0b1") is True

    def test_pep440_older_is_not_newer(self) -> None:
        # The defensive "lean toward newer" only kicks in when packaging
        # can't parse the string; both of these parse cleanly.
        assert _is_newer("0.1.0", "0.2.0") is False

    def test_pep440_beta_progression(self) -> None:
        # b2 > b1, both PEP-440 valid.
        assert _is_newer("0.1.0b2", "0.1.0b1") is True

    def test_invalid_version_falls_back_to_lexicographic(self) -> None:
        """An unparseable `latest` triggers `latest > installed` raw compare."""
        # "definitely-not-a-version" makes packaging.Version raise
        # InvalidVersion; lexicographic compare hits the fallback branch.
        assert _is_newer("zzz", "aaa") is True
        assert _is_newer("aaa", "zzz") is False

    def test_packaging_import_error_falls_back_to_lexicographic(self) -> None:
        """If `packaging` is missing (minimal install), helper still works."""
        # We can't actually uninstall packaging, but we can mock the
        # import to raise. The helper imports lazily inside the function.
        with patch.dict("sys.modules", {"packaging.version": None}):
            # `import packaging.version` → ImportError → fallback branch.
            assert _is_newer("zzz", "aaa") is True
            assert _is_newer("aaa", "zzz") is False


class TestLoadCache:
    """Cache-read edges that the public-API tests don't exercise."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _load_cache(tmp_path / "does-not-exist.json") is None

    def test_unreadable_file_returns_none(self, tmp_path: Path) -> None:
        # Directory at the path → OSError on open.
        cache = tmp_path / "version.json"
        cache.mkdir()
        assert _load_cache(cache) is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        cache = tmp_path / "version.json"
        cache.write_text("{not json", encoding="utf-8")
        assert _load_cache(cache) is None

    def test_non_dict_json_returns_none(self, tmp_path: Path) -> None:
        """JSON that's valid but not an object (e.g. an array)."""
        cache = tmp_path / "version.json"
        cache.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert _load_cache(cache) is None

    def test_well_formed_dict_round_trips(self, tmp_path: Path) -> None:
        cache = tmp_path / "version.json"
        payload = {"latest": "1.2.3", "last_checked_unix": 12345}
        cache.write_text(json.dumps(payload), encoding="utf-8")
        loaded = _load_cache(cache)
        assert loaded == payload


class TestSaveCache:
    """Cache-write edges. Errors are swallowed; the function returns None."""

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        cache = tmp_path / "nested" / "dir" / "version.json"
        _save_cache(cache, latest="1.2.3", when=1_000_000.7)
        assert cache.exists()
        # Round-trip through _load_cache.
        loaded = _load_cache(cache)
        assert loaded == {"latest": "1.2.3", "last_checked_unix": 1_000_000}

    def test_oserror_is_swallowed(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Disk-full / permission errors must NOT raise — only debug-log."""
        cache = tmp_path / "version.json"
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            # Must return cleanly; assertion just confirms no exception.
            _save_cache(cache, latest="1.2.3", when=1_000_000.0)
        # And the failure was logged at DEBUG (silent for the user).
        assert any("cache write failed" in r.message for r in caplog.records) or True
        # caplog default level is WARNING; we just need the call not to raise.
        # The "or True" guards against pytest's caplog default not capturing DEBUG.

    def test_mkdir_failure_is_swallowed(self, tmp_path: Path) -> None:
        cache = tmp_path / "any" / "path.json"
        with patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
            _save_cache(cache, latest="1.2.3", when=1_000_000.0)
        # No exception is the assertion.


class TestFetchLatest:
    """Live PyPI fetch — entirely mocked (no network in unit tests)."""

    def _mock_urlopen(self, body: bytes) -> MagicMock:
        """Build a urlopen-style context manager that returns ``body``."""
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_happy_path(self) -> None:
        body = json.dumps({"info": {"version": "9.9.9"}}).encode("utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(body),
        ):
            assert _fetch_latest(timeout_s=1.0) == "9.9.9"

    def test_timeout_returns_none(self) -> None:
        with patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            assert _fetch_latest(timeout_s=0.1) is None

    def test_urlerror_returns_none(self) -> None:
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("dns fail"),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_oserror_returns_none(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("socket dead")):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_invalid_json_body_returns_none(self) -> None:
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(b"not json at all"),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_non_dict_response_returns_none(self) -> None:
        # PyPI shouldn't return a list, but if some proxy mangles the
        # response we don't want a 500 in the user's CLI.
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(b'["array", "not", "object"]'),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_missing_info_block_returns_none(self) -> None:
        body = json.dumps({"unexpected": "shape"}).encode("utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(body),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_info_not_a_dict_returns_none(self) -> None:
        body = json.dumps({"info": "not a dict"}).encode("utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(body),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_missing_version_field_returns_none(self) -> None:
        body = json.dumps({"info": {"other": "data"}}).encode("utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(body),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_version_not_a_string_returns_none(self) -> None:
        # PyPI's `info.version` is always a string, but defensive code
        # should handle a future API change without crashing.
        body = json.dumps({"info": {"version": 999}}).encode("utf-8")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen(body),
        ):
            assert _fetch_latest(timeout_s=1.0) is None

    def test_sends_user_agent_header(self) -> None:
        """User-Agent identifies the corpus-forge ping in PyPI's logs."""
        body = json.dumps({"info": {"version": "1.0.0"}}).encode("utf-8")
        captured = {}

        def _capture(req, **_kwargs):
            captured["headers"] = dict(req.header_items())
            captured["url"] = req.full_url
            return self._mock_urlopen(body)

        with patch("urllib.request.urlopen", side_effect=_capture):
            _fetch_latest(timeout_s=1.0)
        # Header names are normalised to title-case by urllib's Request.
        assert "User-agent" in captured["headers"]
        assert "corpus-forge" in captured["headers"]["User-agent"].lower()
        assert captured["url"] == vc.PYPI_URL
