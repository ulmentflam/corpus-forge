"""Phase I-10 — ``corpus-forge doctor`` health checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from corpus_forge.doctor import CheckStatus, run_doctor
from corpus_forge.doctor.checks import (
    _check_ffmpeg,
    _check_poppler,
    _check_python_version,
    _check_uv,
)


class TestRunDoctor:
    def test_returns_report_with_checks(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[backend]\nkind = "sqlite"\ndsn = "x"\n', encoding="utf-8")
        report = run_doctor(config_path=cfg)
        assert len(report.results) >= 4
        names = {r.name for r in report.results}
        assert "python" in names
        assert "config" in names

    def test_missing_config_warns(self, tmp_path: Path) -> None:
        report = run_doctor(config_path=tmp_path / "absent.toml")
        config_result = next(r for r in report.results if r.name == "config")
        assert config_result.status == CheckStatus.WARN
        assert "setup" in config_result.detail.lower()

    def test_malformed_config_fails(self, tmp_path: Path) -> None:
        cfg = tmp_path / "broken.toml"
        cfg.write_text("not = valid = toml = at = all", encoding="utf-8")
        report = run_doctor(config_path=cfg)
        config_result = next(r for r in report.results if r.name == "config")
        assert config_result.status == CheckStatus.FAIL
        assert not report.healthy

    def test_render_includes_status_markers(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[backend]\nkind = "sqlite"\ndsn = "x"\n', encoding="utf-8")
        text = run_doctor(config_path=cfg).render()
        assert "corpus-forge doctor" in text
        assert "[OK  ]" in text or "[WARN]" in text or "[FAIL]" in text


# ── individual check helpers ─────────────────────────────────────────────


class TestCheckPython:
    """Exercise the unsupported-version FAIL branch."""

    def test_supported_version_is_ok(self) -> None:
        # Live interpreter is one of 3.11/3.12/3.13 (per CI matrix).
        result = _check_python_version()
        assert result.status == CheckStatus.OK
        assert result.name == "python"

    def test_too_old_python_fails(self) -> None:
        fake = type(
            "VI", (), {"major": 3, "minor": 10, "micro": 13, "releaselevel": "final", "serial": 0}
        )()
        with patch("corpus_forge.doctor.checks.sys.version_info", fake):
            result = _check_python_version()
        assert result.status == CheckStatus.FAIL
        assert "3.10" in result.detail
        assert ">=3.11" in result.detail

    def test_too_new_python_fails(self) -> None:
        fake = type(
            "VI", (), {"major": 3, "minor": 14, "micro": 0, "releaselevel": "final", "serial": 0}
        )()
        with patch("corpus_forge.doctor.checks.sys.version_info", fake):
            result = _check_python_version()
        assert result.status == CheckStatus.FAIL
        assert "3.14" in result.detail


class TestCheckPoppler:
    def test_pdftoppm_present_is_ok(self) -> None:
        with patch("corpus_forge.doctor.checks.shutil.which", return_value="/usr/bin/pdftoppm"):
            result = _check_poppler()
        assert result.status == CheckStatus.OK
        assert "pdftoppm" in result.detail.lower()

    def test_pdftoppm_missing_warns(self) -> None:
        with patch("corpus_forge.doctor.checks.shutil.which", return_value=None):
            result = _check_poppler()
        assert result.status == CheckStatus.WARN
        assert "pdftoppm" in result.detail.lower()
        # WARN message must point the user at the right opt-in extra.
        assert "[ocr]" in result.detail


class TestCheckFfmpeg:
    def test_system_ffmpeg_is_ok(self) -> None:
        with patch("corpus_forge.doctor.checks.shutil.which", return_value="/usr/local/bin/ffmpeg"):
            result = _check_ffmpeg()
        assert result.status == CheckStatus.OK
        assert "ffmpeg on PATH" in result.detail

    def test_imageio_fallback_is_ok(self) -> None:
        """No system ffmpeg, but imageio-ffmpeg is importable."""
        # imageio_ffmpeg is a real dep of the [whisper] extra; create a
        # fake module object so the import succeeds without depending
        # on whether the test environment actually has it installed.
        import sys
        from types import ModuleType

        fake = ModuleType("imageio_ffmpeg")
        fake.__version__ = "0.4.9"  # type: ignore[attr-defined]
        with (
            patch("corpus_forge.doctor.checks.shutil.which", return_value=None),
            patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
        ):
            result = _check_ffmpeg()
        assert result.status == CheckStatus.OK
        assert "imageio-ffmpeg 0.4.9" in result.detail

    def test_no_ffmpeg_anywhere_warns(self) -> None:
        """No system ffmpeg AND imageio-ffmpeg not installed."""
        import sys

        # Force the inner ``import imageio_ffmpeg`` to ImportError.
        with (
            patch("corpus_forge.doctor.checks.shutil.which", return_value=None),
            patch.dict(sys.modules, {"imageio_ffmpeg": None}),
        ):
            result = _check_ffmpeg()
        assert result.status == CheckStatus.WARN
        assert "imageio-ffmpeg" in result.detail


class TestCheckUv:
    def test_uv_on_path_with_version(self) -> None:
        fake_proc = subprocess.CompletedProcess(
            args=["uv", "--version"],
            returncode=0,
            stdout="uv 0.5.7 (1234abc 2026-05-01)\n",
            stderr="",
        )
        with (
            patch("corpus_forge.doctor.checks.shutil.which", return_value="/usr/local/bin/uv"),
            patch("corpus_forge.doctor.checks.subprocess.run", return_value=fake_proc),
        ):
            result = _check_uv()
        assert result.status == CheckStatus.OK
        assert "uv 0.5.7" in result.detail

    def test_uv_on_path_but_version_empty(self) -> None:
        """Defensive: if `uv --version` prints nothing, fall back to a generic OK string."""
        fake_proc = subprocess.CompletedProcess(
            args=["uv", "--version"], returncode=0, stdout="\n", stderr=""
        )
        with (
            patch("corpus_forge.doctor.checks.shutil.which", return_value="/usr/local/bin/uv"),
            patch("corpus_forge.doctor.checks.subprocess.run", return_value=fake_proc),
        ):
            result = _check_uv()
        assert result.status == CheckStatus.OK
        assert result.detail == "uv installed"

    def test_uv_not_on_path_warns(self) -> None:
        with patch("corpus_forge.doctor.checks.shutil.which", return_value=None):
            result = _check_uv()
        assert result.status == CheckStatus.WARN
        assert "self-update" in result.detail

    def test_uv_timeout_warns(self) -> None:
        with (
            patch("corpus_forge.doctor.checks.shutil.which", return_value="/usr/local/bin/uv"),
            patch(
                "corpus_forge.doctor.checks.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="uv --version", timeout=2),
            ),
        ):
            result = _check_uv()
        assert result.status == CheckStatus.WARN
        # Detail names the underlying exception so a user can investigate.
        assert "uv" in result.detail.lower()

    def test_uv_file_not_found_warns(self) -> None:
        """``shutil.which`` lied — binary was moved between the two calls."""
        with (
            patch("corpus_forge.doctor.checks.shutil.which", return_value="/usr/local/bin/uv"),
            patch(
                "corpus_forge.doctor.checks.subprocess.run",
                side_effect=FileNotFoundError("uv: No such file"),
            ),
        ):
            result = _check_uv()
        assert result.status == CheckStatus.WARN
        assert "uv" in result.detail.lower()


# ── Phase M Wave 1 — corpusignore doctor check ────────────────────────


class TestCheckCorpusignore:
    """``_check_corpusignore(cfg)`` follows the standard CheckResult
    pattern: SKIP when no FS-style data root, WARN when the file is
    missing or drifted, FAIL when the file is present but unparseable,
    OK when synced.
    """

    def _write_config(self, tmp_path: Path, scan_root: Path | None = None) -> Path:
        """Render a minimal toml config that loads cleanly.

        Paths are embedded in POSIX form (``as_posix()``) so the TOML
        parser doesn't treat Windows backslashes as escape sequences
        (``\\t``, ``\\U``, etc.). Pydantic's ``ExpandedPath`` accepts
        forward-slash paths on every platform.
        """
        if scan_root is None:
            datasets_block = (
                "[[datasets]]\n"
                'name = "default"\n'
                'kind = "text"\n'
                'sources = [{plugin = "claude_code", projects_root = "'
                + (tmp_path / "claude").as_posix()
                + '", chunker = "conversation"}]\n'
            )
        else:
            datasets_block = (
                "[[datasets]]\n"
                'name = "default"\n'
                'kind = "text"\n'
                'sources = [{plugin = "filesystem", root = "'
                + scan_root.as_posix()
                + '", chunker = "markdown"}]\n'
            )
        cfg_text = (
            '[backend]\nkind = "sqlite"\ndsn = ":memory:"\n\n'
            "[daemon]\n\n"
            + datasets_block
            + '\n[[embedders]]\nname = "x"\nprovider = "sentence_transformers"\n'
            'model_id = "x"\ndimension = 8\n'
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text, encoding="utf-8")
        return cfg_path

    def test_skip_when_no_fs_root(self, tmp_path: Path) -> None:
        from corpus_forge.config import Config
        from corpus_forge.doctor.checks import _check_corpusignore

        cfg_path = self._write_config(tmp_path, scan_root=None)
        cfg = Config.load(config_path=cfg_path, secrets_path=tmp_path / "secrets.env")
        result = _check_corpusignore(cfg)
        assert result.name == "corpusignore"
        assert result.status == CheckStatus.SKIP

    def test_warn_when_file_missing(self, tmp_path: Path) -> None:
        from corpus_forge.config import Config
        from corpus_forge.doctor.checks import _check_corpusignore

        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        cfg_path = self._write_config(tmp_path, scan_root=scan_root)
        cfg = Config.load(config_path=cfg_path, secrets_path=tmp_path / "secrets.env")
        result = _check_corpusignore(cfg)
        assert result.status == CheckStatus.WARN
        assert "ignore init" in result.detail.lower() or "ignore sync" in result.detail.lower()

    def test_fail_on_unparseable_line(self, tmp_path: Path) -> None:
        """Make ``CorpusIgnore.from_file`` raise OSError via an unreadable path."""
        from unittest.mock import patch as _patch

        from corpus_forge.config import Config
        from corpus_forge.doctor.checks import _check_corpusignore

        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        # Plant a .corpusignore so it's "present" (not WARN-missing)
        # but force the parse path to raise.
        (scan_root / ".corpusignore").write_text("# valid\n*.txt\n", encoding="utf-8")
        cfg_path = self._write_config(tmp_path, scan_root=scan_root)
        cfg = Config.load(config_path=cfg_path, secrets_path=tmp_path / "secrets.env")
        with _patch(
            "corpus_forge.ignore.CorpusIgnore.from_file",
            side_effect=OSError("parse failure at line 3"),
        ):
            result = _check_corpusignore(cfg)
        assert result.status == CheckStatus.FAIL
        assert "parse" in result.detail.lower() or "line" in result.detail.lower()

    def test_warn_on_managed_block_drift(self, tmp_path: Path) -> None:
        """Synced file under whisper=off, then config flipped to whisper=on
        → managed block drift → WARN."""
        from corpus_forge.config import Config
        from corpus_forge.doctor.checks import _check_corpusignore
        from corpus_forge.ignore_lifecycle import write_corpusignore

        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        # Sync against whisper=off.
        write_corpusignore(scan_root, {"whisper": False})
        # Now run doctor against a config with whisper=on — drift expected.
        cfg_text = (
            '[backend]\nkind = "sqlite"\ndsn = ":memory:"\n\n'
            "[daemon]\n\n"
            "[[datasets]]\n"
            'name = "default"\n'
            'kind = "text"\n'
            'sources = [{plugin = "filesystem", root = "'
            + scan_root.as_posix()
            + '", chunker = "markdown"}]\n\n'
            "[[embedders]]\n"
            'name = "x"\nprovider = "sentence_transformers"\nmodel_id = "x"\ndimension = 8\n\n'
            '[whisper]\nbackend = "local"\nmodel = "small"\n'
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(cfg_text, encoding="utf-8")
        cfg = Config.load(config_path=cfg_path, secrets_path=tmp_path / "secrets.env")
        result = _check_corpusignore(cfg)
        assert result.status == CheckStatus.WARN
        assert "sync" in result.detail.lower() or "drift" in result.detail.lower()

    def test_ok_when_synced(self, tmp_path: Path) -> None:
        from corpus_forge.config import Config
        from corpus_forge.doctor.checks import _check_corpusignore
        from corpus_forge.ignore_lifecycle import write_corpusignore

        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        # Sync under whisper=off, then verify under same config.
        write_corpusignore(scan_root, {"whisper": False})
        cfg_path = self._write_config(tmp_path, scan_root=scan_root)
        cfg = Config.load(config_path=cfg_path, secrets_path=tmp_path / "secrets.env")
        result = _check_corpusignore(cfg)
        assert result.status == CheckStatus.OK


class TestDoctorJsonShapePreserved:
    """Phase M Wave 1 — the new ``corpusignore`` check is additive only.
    Existing JSON ``checks[]`` keys all still present + a new entry for
    the corpusignore check.
    """

    def test_corpusignore_check_appears_in_report(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text('[backend]\nkind = "sqlite"\ndsn = ":memory:"\n', encoding="utf-8")
        report = run_doctor(config_path=cfg_path)
        names = {r.name for r in report.results}
        # Pre-existing names still present.
        assert "python" in names
        assert "config" in names
        # New name.
        assert "corpusignore" in names


# ── 2026-05-27 — global managed-ignore drift check ────────────────────


class TestCheckGlobalIgnoreDrift:
    """``_check_global_ignore`` compares the user-global ignore file's
    managed block against the current template and WARNs on drift,
    mirroring the ``embedder_indexes`` idiom (WARN + exact repair
    command, no silent mutation from the audit path).

    The global file lives at ``~/.config/corpus-forge/ignore`` but
    honors ``CF_GLOBAL_IGNORE_FILE``; the tests point that env var at a
    tmp path so the real ``~/.config`` is never touched.
    """

    def test_skip_when_global_file_absent(self, tmp_path: Path, monkeypatch) -> None:
        from corpus_forge.doctor.checks import _check_global_ignore

        global_path = tmp_path / "ignore"  # does not exist
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(global_path))
        result = _check_global_ignore()
        assert result.name == "global_ignore"
        assert result.status == CheckStatus.SKIP

    def test_skip_when_no_managed_block(self, tmp_path: Path, monkeypatch) -> None:
        # A global file the user created by hand with no sentinels at
        # all — nothing for us to compare, so SKIP (not WARN).
        from corpus_forge.doctor.checks import _check_global_ignore

        global_path = tmp_path / "ignore"
        global_path.write_text("# my own patterns\nSecret/\n", encoding="utf-8")
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(global_path))
        result = _check_global_ignore()
        assert result.status == CheckStatus.SKIP

    def test_ok_when_global_block_matches_template(self, tmp_path: Path, monkeypatch) -> None:
        from corpus_forge.doctor.checks import _check_global_ignore
        from corpus_forge.ignore_defaults import render_managed_block

        global_path = tmp_path / "ignore"
        # Render with all features off (the conservative preset the
        # wizard/sync use for the global file).
        global_path.write_text(
            render_managed_block(
                {"whisper": False, "image_extractor": False, "code_enricher": False, "vlm": False}
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(global_path))
        result = _check_global_ignore()
        assert result.status == CheckStatus.OK

    def test_warn_when_global_block_missing_junk_patterns(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Simulate a STALE managed block written before the dev/build
        # junk patterns existed: a sentinel-wrapped block that omits
        # ``.venv/`` / ``node_modules/`` / ``__pycache__/`` etc.
        from corpus_forge.doctor.checks import _check_global_ignore
        from corpus_forge.ignore_defaults import MANAGED_END, MANAGED_START

        stale = (
            f"{MANAGED_START}\n"
            "# Generated 2026-01-01T00:00:00+00:00 — do not edit; managed by corpus-forge.\n"
            ".DS_Store\n"
            "*.icloud\n"
            "build/\n"
            "dist/\n"
            f"{MANAGED_END}\n"
        )
        global_path = tmp_path / "ignore"
        global_path.write_text(stale, encoding="utf-8")
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(global_path))
        result = _check_global_ignore()
        assert result.status == CheckStatus.WARN
        # The check must name the one-command fix.
        assert "ignore sync" in result.detail.lower()
        assert "--also-global" in result.detail

    def test_warn_does_not_mutate_the_file(self, tmp_path: Path, monkeypatch) -> None:
        # The audit path must NOT silently rewrite user config.
        from corpus_forge.doctor.checks import _check_global_ignore
        from corpus_forge.ignore_defaults import MANAGED_END, MANAGED_START

        stale = f"{MANAGED_START}\n.DS_Store\n{MANAGED_END}\n"
        global_path = tmp_path / "ignore"
        global_path.write_text(stale, encoding="utf-8")
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(global_path))
        result = _check_global_ignore()
        assert result.status == CheckStatus.WARN
        # File untouched.
        assert global_path.read_text(encoding="utf-8") == stale

    def test_global_ignore_appears_in_full_report(self, tmp_path: Path, monkeypatch) -> None:
        # Additive to the orchestrated report; existing names survive.
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(tmp_path / "ignore"))
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text('[backend]\nkind = "sqlite"\ndsn = ":memory:"\n', encoding="utf-8")
        report = run_doctor(config_path=cfg_path)
        names = {r.name for r in report.results}
        assert "python" in names
        assert "config" in names
        assert "corpusignore" in names
        assert "global_ignore" in names
