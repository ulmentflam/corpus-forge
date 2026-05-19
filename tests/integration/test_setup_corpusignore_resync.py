"""Phase M Wave 1 — integration tests for the ``.corpusignore``
resync lifecycle.

Scenarios:

1. First run with ``whisper=no`` adds the audio patterns.
2. Re-run with ``whisper=yes`` removes them, but a user-added line
   below the closing sentinel survives.
3. Deleted ``.corpusignore`` is recreated by the next run.
4. Corrupted sentinels trigger a ``.bak.<ts>`` backup + a full rewrite.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.ignore_defaults import MANAGED_END, MANAGED_START
from corpus_forge.setup import run_non_interactive


def _env(*, scan_root: Path, whisper: str) -> dict[str, str]:
    env: dict[str, str] = {
        "CF_BACKEND": "sqlite",
        "CF_MULTI_FORMAT": "no",
        "CF_CODE": "no",
        "CF_OCR": "no",
        "CF_WHISPER": whisper,
        "CF_TOKENS": "no",
        "CF_RETRIEVAL": "no",
        "CF_RERANKER": "no",
        "CF_EMBEDDER": "st",
        "CF_CLASSIFIER": "rule",
        "CF_MCP": "no",
        "CF_HF": "no",
        "CF_SUPERVISOR": "no",
        "CF_CREATE_CORPUSIGNORE": "yes",
        "CF_SCAN_ROOT": str(scan_root),
    }
    if whisper == "yes":
        env["CF_WHISPER_BACKEND"] = "local"
        env["CF_WHISPER_LOCAL_MODEL"] = "small"
    return env


def test_feature_flip_updates_managed_block_preserves_user_lines(tmp_path: Path) -> None:
    scan_root = tmp_path / "vault"
    scan_root.mkdir()
    config_dir = tmp_path / "cf"

    # First pass — whisper off, audio patterns land inside.
    run_non_interactive(config_dir=config_dir, env=_env(scan_root=scan_root, whisper="no"))
    ignore_path = scan_root / ".corpusignore"
    assert ignore_path.exists()
    text = ignore_path.read_text(encoding="utf-8")
    assert "*.mp4" in text

    # User appends their own pattern below the closing sentinel.
    with ignore_path.open("a", encoding="utf-8") as fp:
        fp.write("\n# my user line\nMyVault/Drafts/\n")

    # Second pass — whisper on, audio patterns drop out.
    run_non_interactive(config_dir=config_dir, env=_env(scan_root=scan_root, whisper="yes"))
    text2 = ignore_path.read_text(encoding="utf-8")
    assert "MyVault/Drafts/" in text2, "user line below closing sentinel must survive"
    assert "*.mp4" not in text2


def test_deleted_corpusignore_is_recreated(tmp_path: Path) -> None:
    scan_root = tmp_path / "vault"
    scan_root.mkdir()
    config_dir = tmp_path / "cf"
    run_non_interactive(config_dir=config_dir, env=_env(scan_root=scan_root, whisper="no"))
    ignore_path = scan_root / ".corpusignore"
    assert ignore_path.exists()
    # User nukes it.
    ignore_path.unlink()
    # Re-run the wizard.
    run_non_interactive(config_dir=config_dir, env=_env(scan_root=scan_root, whisper="no"))
    assert ignore_path.exists()


def test_corrupted_sentinels_trigger_backup_and_rewrite(tmp_path: Path) -> None:
    scan_root = tmp_path / "vault"
    scan_root.mkdir()
    config_dir = tmp_path / "cf"

    # Seed a half-closed file.
    ignore_path = scan_root / ".corpusignore"
    ignore_path.write_text(
        f"# user pinned this\nUserKept/\n{MANAGED_START}\nold-managed-pat\n# no closing\n",
        encoding="utf-8",
    )

    # Run setup — the lifecycle layer should:
    #   1. Detect the broken sentinels.
    #   2. Move the broken file aside as `.corpusignore.bak.<ts>`.
    #   3. Rewrite the .corpusignore from scratch.
    run_non_interactive(config_dir=config_dir, env=_env(scan_root=scan_root, whisper="no"))

    # Backup left behind.
    backups = [p for p in scan_root.iterdir() if p.name.startswith(".corpusignore.bak.")]
    assert len(backups) >= 1
    # File rewritten cleanly.
    text = ignore_path.read_text(encoding="utf-8")
    assert MANAGED_START in text
    assert MANAGED_END in text
    # Original broken body is NOT in the rewritten file.
    assert "old-managed-pat" not in text
