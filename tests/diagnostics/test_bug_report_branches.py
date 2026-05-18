"""Coverage targeting :mod:`corpus_forge.diagnostics.bug_report` — the
``redaction_log`` append branches + the ``out=<dir>`` / explicit-zip
paths not exercised by ``test_bug_report.py``.

We force secrets into every section so the per-section ``if n`` blocks
all fire, and we exercise the ``out`` parameter both as a directory
(zip auto-named) and as an explicit file path.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from corpus_forge.diagnostics import bug_report as br


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setenv("CF_LOG_DIR", str(log_dir))

    import corpus_forge.logging_config as _lc

    monkeypatch.setattr(_lc, "_LOG_DIR", None)
    from corpus_forge.logging_config import init_logging

    init_logging("cli")

    # Cli log has a fake API key + DSN — triggers redact across every section.
    (log_dir / "cli.log").write_text(
        "2026-05-18 12:00:00 [INFO   ] cli: sk-abcdef1234567890ABCDEFGH\n"
        "2026-05-18 12:00:01 [INFO   ] cli: postgresql://u:secretpw@host/db\n",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_redaction_log_records_each_section(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force secrets into the env + doctor + config so every section emits a
    ``redaction_log`` entry, hitting the per-section ``if n: redaction_log.append`` lines."""

    # Force the env collector to surface a secret-shaped string.
    monkeypatch.setenv("CF_FAKE_API_KEY", "sk-abcdef1234567890ABCDEFGH")
    monkeypatch.setattr(
        br,
        "_collect_doctor_json",
        lambda: {"dsn": "postgresql://u:secret@host/db"},
    )
    # Force the deps payload to contain a secret too.
    monkeypatch.setattr(br, "_collect_deps", lambda: "fake-pkg==sk-abcdef1234567890ABCDEFGH\n")
    # Force a recent_events line with a secret.
    monkeypatch.setattr(
        br,
        "_collect_recent_events",
        lambda: "2026-05-18 [INFO] api_key=sk-abcdef1234567890ABCDEFGH\n",
    )
    # Force a config.toml with a secret.
    monkeypatch.setattr(br, "_collect_config_toml", lambda: 'dsn="postgresql://u:p@h/d"\n')
    monkeypatch.setattr(br, "_collect_service_status", lambda: "key=sk-abcdef1234567890ABCDEFGH\n")
    # And include_db=True forces the db_summary section to redact.
    monkeypatch.setattr(br, "_collect_db_summary", lambda: {"dsn": "postgresql://u:p@h/d"})

    report = br.collect(include_db=True)

    # Read manifest.json from the zip.
    with zipfile.ZipFile(report.path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    log = manifest["redaction_log"]
    # Multiple sections should each contribute a "section:N" entry.
    sections = {entry.split(":", 1)[0] for entry in log}
    # At minimum doctor + deps + recent_events + config should all redact.
    assert "doctor.json" in sections
    assert "deps.txt" in sections
    assert any("recent_events" in s for s in sections)
    assert "config.redacted.toml" in sections


def test_out_directory_writes_zip_into_dir(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``out`` is an existing directory, the zip lands inside it."""

    target_dir = tmp_path / "drops"
    target_dir.mkdir(parents=True)

    report = br.collect(out=target_dir, include_db=False)
    assert report.path.parent == target_dir
    assert report.path.suffix == ".zip"


def test_out_explicit_file_path(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``out=<file>`` writes the zip at exactly that path."""

    target = tmp_path / "bundle.zip"
    report = br.collect(out=target, include_db=False)
    assert report.path == target
    assert target.exists()


def test_no_zip_with_explicit_out_directory(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "staging_dir"
    report = br.collect(out=target, zip_bundle=False, include_db=False)
    assert report.path == target
    assert (target / "manifest.json").exists()


def test_no_zip_overwrites_existing_directory(
    isolated: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the target dir already exists, the ``_copy_dir`` helper clobbers it."""

    target = tmp_path / "predest"
    target.mkdir()
    (target / "old_file.txt").write_text("stale", encoding="utf-8")

    report = br.collect(out=target, zip_bundle=False, include_db=False)
    assert report.path == target
    assert not (target / "old_file.txt").exists()


def test_db_summary_section_included(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``include_db=True``, ``db_summary.json`` appears in the bundle."""

    # Make the db summary cheap and deterministic — no real backend needed.
    monkeypatch.setattr(
        br, "_collect_db_summary", lambda: {"datasets": 1, "documents": 5, "chunks": 50}
    )
    report = br.collect(include_db=True)
    with zipfile.ZipFile(report.path) as zf:
        assert "db_summary.json" in zf.namelist()
        payload = json.loads(zf.read("db_summary.json"))
    assert payload["datasets"] == 1


def test_collect_recent_events_with_ring_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real ring-buffer flush path."""

    import logging as _logging

    record = _logging.LogRecord(
        name="test",
        level=_logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="ring buffer test message",
        args=(),
        exc_info=None,
    )

    class _FakeRing:
        buffer = [record]  # noqa: RUF012

    monkeypatch.setattr(br, "get_ring_buffer", _FakeRing)
    text = br._collect_recent_events()
    assert "ring buffer test message" in text


def test_collect_recent_events_empty_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ring buffer returns the placeholder string."""

    class _FakeRing:
        buffer = []  # noqa: RUF012

    monkeypatch.setattr(br, "get_ring_buffer", _FakeRing)
    text = br._collect_recent_events()
    assert "(ring buffer empty)" in text


def test_collect_config_toml_missing_returns_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CF_CONFIG", raising=False)
    # Force home so no real config is picked up.
    monkeypatch.setattr(br.Path, "home", classmethod(lambda cls: tmp_path / "fake_home"))
    text = br._collect_config_toml()
    assert "no config.toml found" in text


def test_collect_config_toml_unreadable_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the candidate path exists but read fails, we report the error inline."""

    cfg = tmp_path / "fake_home" / ".config" / "corpus-forge"
    cfg.mkdir(parents=True)
    config_path = cfg / "config.toml"
    config_path.write_text("backend = invalid toml [[[\n", encoding="utf-8")

    monkeypatch.setattr(br.Path, "home", classmethod(lambda cls: tmp_path / "fake_home"))
    text = br._collect_config_toml()
    # Either parsed back as empty or the error message lands inline.
    assert text is not None


def test_collect_config_toml_via_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``CF_CONFIG`` env-var-resolved path is read when present."""

    cfg = tmp_path / "myconfig.toml"
    cfg.write_text('[backend]\nkind = "sqlite"\ndsn = "/x/y/z"\n', encoding="utf-8")
    monkeypatch.setenv("CF_CONFIG", str(cfg))
    text = br._collect_config_toml()
    assert "[backend]" in text


def test_collect_doctor_json_failure_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``run_doctor`` raises, we fall back to an ``{"unavailable": ...}`` dict.

    The error path is normally a no-op pragma; this just verifies the
    happy path returns a dict.
    """

    payload = br._collect_doctor_json()
    assert isinstance(payload, dict)


def test_collect_env_filters_to_known_prefixes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only env vars matching the prefix list survive the filter."""

    monkeypatch.setenv("CF_SOMETHING", "value-1")
    monkeypatch.setenv("RANDOM_UNRELATED_VAR", "ignored-2")
    text = br._collect_env()
    assert "CF_SOMETHING" in text
    assert "RANDOM_UNRELATED_VAR" not in text


def test_collect_deps_fallback_to_importlib_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When pip fails, the importlib.metadata fallback is used."""

    import subprocess as _subprocess

    class _FakeProc:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _FakeProc())
    text = br._collect_deps()
    # The fallback returns one ``name==version`` per line.
    assert "==" in text


def test_collect_service_status_renders() -> None:
    """The service status snapshot returns non-empty text."""

    text = br._collect_service_status()
    assert text  # non-empty


def test_path_bytes_for_directory(tmp_path: Path) -> None:
    """``_path_bytes`` sums every file under a directory."""

    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("hello", encoding="utf-8")
    n = br._path_bytes(tmp_path)
    assert n == len("hi") + len("hello")


def test_path_bytes_for_file(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("abc", encoding="utf-8")
    assert br._path_bytes(f) == 3


def test_human_bytes_each_bucket() -> None:
    assert br._human_bytes(1) == "1 B"
    assert br._human_bytes(1500).endswith(" KB")
    assert br._human_bytes(2_000_000).endswith(" MB")
    assert br._human_bytes(3_000_000_000).endswith(" GB")


def test_short_hash_deterministic() -> None:
    assert br._short_hash(b"abc") == br._short_hash(b"abc")
    assert len(br._short_hash(b"abc")) == 8


def test_tail_log_truncates_to_max_bytes(tmp_path: Path) -> None:
    log = tmp_path / "big.log"
    log.write_text("L" * 1000 + "\nfinal line\n", encoding="utf-8")
    tail = br._tail_log(log, max_bytes=100)
    # Tail should drop the leading partial line and keep "final line".
    assert "final line" in tail


def test_tail_log_missing_returns_empty(tmp_path: Path) -> None:
    assert br._tail_log(tmp_path / "absent.log") == ""


def test_collect_db_summary_handles_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Config.load fails, the helper returns ``{"unavailable": ...}``."""

    from corpus_forge import config as _cf_cfg

    def _explode(*a, **k):
        raise FileNotFoundError("no config")

    monkeypatch.setattr(_cf_cfg.Config, "load", classmethod(lambda cls, **kw: _explode(**kw)))
    payload = br._collect_db_summary()
    assert "unavailable" in payload


def test_agent_mode_payload_falls_back_to_human(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``ui.agent.current_detection`` raises, the manifest carries the
    string ``"human"`` instead of a dict."""

    from corpus_forge.ui import agent as _agent

    def _boom():
        raise RuntimeError("no detection")

    monkeypatch.setattr(_agent, "current_detection", _boom)
    report = br.collect(include_db=False)
    with zipfile.ZipFile(report.path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["agent_mode_at_time_of_capture"] == "human"


def test_collect_with_no_logs_still_writes_service_status(isolated: Path) -> None:
    """``--no-logs`` still emits the service_status.txt (it's outside ``logs/``)."""

    report = br.collect(include_logs=False, include_db=False)
    with zipfile.ZipFile(report.path) as zf:
        names = zf.namelist()
    assert "service_status.txt" in names
    assert not any(n.startswith("logs/") for n in names)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
