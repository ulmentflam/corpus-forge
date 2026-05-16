"""Phase I-10 — ``corpus-forge doctor`` health checks."""

from __future__ import annotations

from pathlib import Path

from corpus_forge.doctor import CheckStatus, run_doctor


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
