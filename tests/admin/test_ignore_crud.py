"""Tests for :mod:`corpus_forge.admin.ignore` (Phase M Wave 3).

Covers the seven Typer verbs (``list`` / ``add`` / ``remove`` / ``edit``
/ ``validate`` / ``sync`` / ``init``) plus the reusable helper functions
that the MCP layer also calls.

The fixtures seed a tmp directory with a ``.corpusignore`` containing a
managed block (the canonical Wave 1 shape) and a user-written line below
the closing sentinel.  We then exercise both the CLI surface (via
``CliRunner``) and the module helpers directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

# Module-under-test — RED phase: import resolves once Wave 3 ships.
from corpus_forge.admin import ignore as admin_ignore
from corpus_forge.ignore_defaults import (
    MANAGED_END,
    MANAGED_START,
    default_managed_lines,
)

runner = CliRunner()


# ── helpers ─────────────────────────────────────────────────────────────


_FEATURES_ALL_ON: dict[str, bool] = {
    "whisper": True,
    "image_extractor": True,
    "code_enricher": True,
    "vlm": True,
}

_FEATURES_ALL_OFF: dict[str, bool] = {
    "whisper": False,
    "image_extractor": False,
    "code_enricher": False,
    "vlm": False,
}


def _render_managed(lines: list[str]) -> str:
    """Compose a managed block from explicit lines (skips the timestamp)."""

    body = "\n".join(lines)
    return f"{MANAGED_START}\n{body}\n{MANAGED_END}\n"


def _seed_corpusignore(
    root: Path,
    *,
    managed_lines: list[str] | None = None,
    user_lines: list[str] | None = None,
) -> Path:
    """Write a ``.corpusignore`` under ``root`` with a managed block + user tail."""

    root.mkdir(parents=True, exist_ok=True)
    target = root / ".corpusignore"
    parts: list[str] = []
    if managed_lines is None:
        managed_lines = ["*.lock", "dist/"]
    parts.append(_render_managed(managed_lines))
    if user_lines:
        parts.append("\n".join(user_lines) + "\n")
    target.write_text("".join(parts), encoding="utf-8")
    return target


# ── resolve_local_path / resolve_global_path ───────────────────────────


def test_resolve_local_path_defaults_to_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert admin_ignore.resolve_local_path() == tmp_path / ".corpusignore"


def test_resolve_local_path_honours_explicit(tmp_path: Path) -> None:
    assert admin_ignore.resolve_local_path(tmp_path) == tmp_path / ".corpusignore"


def test_resolve_global_path_honours_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-global"
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(custom))
    assert admin_ignore.resolve_global_path() == custom


def test_resolve_global_path_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CF_GLOBAL_IGNORE_FILE", raising=False)
    expected = Path.home() / ".config" / "corpus-forge" / "ignore"
    assert admin_ignore.resolve_global_path() == expected


# ── list_patterns ──────────────────────────────────────────────────────


def test_list_patterns_local_marks_managed_and_user(tmp_path: Path) -> None:
    _seed_corpusignore(
        tmp_path,
        managed_lines=["*.lock", "dist/"],
        user_lines=["my-secret.json"],
    )
    entries = admin_ignore.list_patterns("local", path=tmp_path)
    by_pat = {e.pattern: e for e in entries}
    assert "*.lock" in by_pat
    assert by_pat["*.lock"].managed is True
    assert by_pat["*.lock"].source == "local"
    assert "my-secret.json" in by_pat
    assert by_pat["my-secret.json"].managed is False
    # Line numbers preserved (1-based).
    assert by_pat["my-secret.json"].line > by_pat["*.lock"].line


def test_list_patterns_global_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = tmp_path / "g"
    g.write_text("global-pat.txt\n", encoding="utf-8")
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(g))
    entries = admin_ignore.list_patterns("global")
    sources = {e.source for e in entries}
    assert sources == {"global"}
    assert any(e.pattern == "global-pat.txt" for e in entries)


def test_list_patterns_all_interleaves_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=["local-only"])
    g = tmp_path / "g"
    g.write_text("global-only\n", encoding="utf-8")
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(g))
    entries = admin_ignore.list_patterns("all", path=tmp_path)
    sources = {e.source for e in entries}
    assert sources == {"local", "global"}
    pats = {e.pattern for e in entries}
    assert {"*.lock", "local-only", "global-only"}.issubset(pats)


# ── CLI: ignore list ───────────────────────────────────────────────────


def test_cli_list_local_provenance(tmp_path: Path) -> None:
    _seed_corpusignore(
        tmp_path,
        managed_lines=["*.lock"],
        user_lines=["my-secret.json"],
    )
    res = runner.invoke(
        admin_ignore.ignore_app,
        ["list", "--local", "--path", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    # Each pattern emitted with [scope] / [scope:managed] prefix.
    out = res.output
    assert "[local:managed] *.lock" in out
    assert "[local] my-secret.json" in out


def test_cli_list_global_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    g = tmp_path / "g"
    g.write_text("only-global.txt\n", encoding="utf-8")
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(g))
    res = runner.invoke(admin_ignore.ignore_app, ["list", "--global"])
    assert res.exit_code == 0, res.output
    assert "[global] only-global.txt" in res.output
    assert "[local]" not in res.output


def test_cli_list_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=["local-only"])
    g = tmp_path / "g"
    g.write_text("global-only\n", encoding="utf-8")
    monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(g))
    res = runner.invoke(
        admin_ignore.ignore_app,
        ["list", "--all", "--path", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    assert "[local:managed] *.lock" in res.output
    assert "[local] local-only" in res.output
    assert "[global] global-only" in res.output


# ── add_pattern + CLI add ──────────────────────────────────────────────


def test_add_pattern_appends_below_closing_sentinel(tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=[])
    before = target.read_text(encoding="utf-8")
    result = admin_ignore.add_pattern("*.tmp", scope="local", path=tmp_path)
    assert result.added is True
    after = target.read_text(encoding="utf-8")
    # Managed block bytes preserved verbatim.
    assert _render_managed(["*.lock"]) in after
    # Pattern appears AFTER the closing sentinel.
    end_idx = after.index(MANAGED_END)
    tail = after[end_idx:]
    assert "*.tmp" in tail
    assert "*.tmp" not in before


def test_add_pattern_idempotent_duplicate(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    r1 = admin_ignore.add_pattern("*.tmp", scope="local", path=tmp_path)
    r2 = admin_ignore.add_pattern("*.tmp", scope="local", path=tmp_path)
    assert r1.added is True
    assert r2.added is False  # idempotent no-op


def test_cli_add_idempotent_exits_zero(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    res1 = runner.invoke(
        admin_ignore.ignore_app,
        ["add", "*.tmp", "--local", "--path", str(tmp_path)],
    )
    assert res1.exit_code == 0, res1.output
    res2 = runner.invoke(
        admin_ignore.ignore_app,
        ["add", "*.tmp", "--local", "--path", str(tmp_path)],
    )
    assert res2.exit_code == 0, res2.output
    assert "already" in res2.output.lower() or "no-op" in res2.output.lower()


def test_cli_add_invalid_pattern_exits_one_and_preserves_file(tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    before = target.read_text(encoding="utf-8")
    # ``(((`` triggers a regex compile failure in _compile_pattern's translation.
    res = runner.invoke(
        admin_ignore.ignore_app,
        ["add", "(((", "--local", "--path", str(tmp_path)],
    )
    assert res.exit_code == 1, res.output
    after = target.read_text(encoding="utf-8")
    assert after == before


# ── remove_pattern + CLI remove ────────────────────────────────────────


def test_remove_pattern_below_sentinel_succeeds(tmp_path: Path) -> None:
    _seed_corpusignore(
        tmp_path,
        managed_lines=["*.lock"],
        user_lines=["*.tmp"],
    )
    r = admin_ignore.remove_pattern("*.tmp", scope="local", path=tmp_path)
    assert r.removed is True
    text = (tmp_path / ".corpusignore").read_text(encoding="utf-8")
    # User-region pattern gone, managed block intact.
    end_idx = text.index(MANAGED_END)
    tail = text[end_idx:]
    assert "*.tmp" not in tail


def test_remove_pattern_idempotent_no_op(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    r = admin_ignore.remove_pattern("*.tmp", scope="local", path=tmp_path)
    assert r.removed is False  # nothing to do; exit 0 in CLI


def test_remove_pattern_inside_managed_raises(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    with pytest.raises(admin_ignore.ManagedRegionProtected):
        admin_ignore.remove_pattern("*.lock", scope="local", path=tmp_path)


def test_cli_remove_managed_pattern_exits_three(tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    before = target.read_text(encoding="utf-8")
    res = runner.invoke(
        admin_ignore.ignore_app,
        ["remove", "*.lock", "--local", "--path", str(tmp_path)],
    )
    assert res.exit_code == 3, res.output
    assert "managed_block_protected" in res.output
    after = target.read_text(encoding="utf-8")
    assert after == before


def test_cli_remove_idempotent_exits_zero(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=["nope.txt"])
    res = runner.invoke(
        admin_ignore.ignore_app,
        ["remove", "missing-pattern", "--local", "--path", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output


# ── validate_file + CLI validate ───────────────────────────────────────


def test_validate_file_clean(tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    r = admin_ignore.validate_file(target)
    assert r.ok is True
    assert r.line is None
    assert r.reason is None


def test_validate_file_reports_failing_line(tmp_path: Path) -> None:
    target = tmp_path / ".corpusignore"
    # Line 1 OK, line 2 OK, line 3 is a broken regex character class.
    target.write_text("ok-one\nok-two\n[(broken\n", encoding="utf-8")
    r = admin_ignore.validate_file(target)
    assert r.ok is False
    assert r.line == 3
    assert "broken" in (r.pattern or "")


def test_cli_validate_clean_exits_zero(tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    res = runner.invoke(admin_ignore.ignore_app, ["validate", str(target)])
    assert res.exit_code == 0, res.output


def test_cli_validate_bad_line_exits_one(tmp_path: Path) -> None:
    target = tmp_path / ".corpusignore"
    target.write_text("ok\nok\n[(broken\n", encoding="utf-8")
    res = runner.invoke(admin_ignore.ignore_app, ["validate", str(target)])
    assert res.exit_code == 1, res.output
    assert "line" in res.output.lower() and "3" in res.output


# ── sync_managed + CLI sync ────────────────────────────────────────────


def test_sync_managed_regenerates_stale_block_preserves_user_lines(
    tmp_path: Path,
) -> None:
    target = _seed_corpusignore(
        tmp_path,
        managed_lines=["stale-pattern-xyz"],  # NOT in default_managed_lines
        user_lines=["my-user-pat.txt"],
    )
    result = admin_ignore.sync_managed(root=tmp_path)
    assert target in result.updated
    text = target.read_text(encoding="utf-8")
    assert "stale-pattern-xyz" not in text  # regenerated
    # Default always-on patterns now appear.
    defaults = default_managed_lines(_FEATURES_ALL_OFF)
    assert any(d in text for d in defaults)
    # User line preserved.
    assert "my-user-pat.txt" in text
    # Idempotent.
    result2 = admin_ignore.sync_managed(root=tmp_path)
    text_after = target.read_text(encoding="utf-8")
    assert target in result2.updated
    # Second sync only changes the timestamp comment (if any); user line
    # remains.
    assert "my-user-pat.txt" in text_after


def test_cli_sync_regenerates(tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["stale-only"], user_lines=["keep-me"])
    res = runner.invoke(admin_ignore.ignore_app, ["sync", "--root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    text = target.read_text(encoding="utf-8")
    assert "stale-only" not in text
    assert "keep-me" in text


# ── init_file + CLI init ───────────────────────────────────────────────


def test_init_file_creates_starter(tmp_path: Path) -> None:
    path = admin_ignore.init_file(root=tmp_path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert MANAGED_START in text and MANAGED_END in text


def test_init_file_refuses_existing(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    with pytest.raises(FileExistsError):
        admin_ignore.init_file(root=tmp_path)


def test_init_file_force_overwrites(tmp_path: Path) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=["custom"])
    path = admin_ignore.init_file(root=tmp_path, force=True)
    text = path.read_text(encoding="utf-8")
    # Force overwrites the user's content with a fresh starter (no "custom").
    assert "custom" not in text
    assert MANAGED_START in text


def test_cli_init_creates_then_refuses_then_force(tmp_path: Path) -> None:
    res1 = runner.invoke(admin_ignore.ignore_app, ["init", "--root", str(tmp_path)])
    assert res1.exit_code == 0, res1.output
    assert (tmp_path / ".corpusignore").exists()

    res2 = runner.invoke(admin_ignore.ignore_app, ["init", "--root", str(tmp_path)])
    assert res2.exit_code == 3, res2.output

    res3 = runner.invoke(admin_ignore.ignore_app, ["init", "--root", str(tmp_path), "--force"])
    assert res3.exit_code == 0, res3.output


# ── ambiguous scope under agent mode ───────────────────────────────────


def test_cli_add_ambiguous_scope_under_agent_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without --local / --global, agent mode must refuse with kind=ambiguous_scope."""

    _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    monkeypatch.setenv("CF_AGENT", "generic")
    from corpus_forge.ui import agent as agent_mod

    agent_mod.set_current(agent_mod.detect())

    res = runner.invoke(
        admin_ignore.ignore_app,
        ["add", "*.tmp", "--path", str(tmp_path)],
    )
    assert res.exit_code == 2, res.output
    # JSONL error event on stdout (agent surface).
    found = False
    for raw_line in res.output.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            evt = json.loads(line)
        except Exception:
            continue
        if evt.get("event") == "error" and evt.get("kind") == "ambiguous_scope":
            found = True
            break
    assert found, f"expected ambiguous_scope error event; got: {res.output!r}"


def test_cli_remove_ambiguous_scope_under_agent_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=["*.tmp"])
    monkeypatch.setenv("CF_AGENT", "generic")
    from corpus_forge.ui import agent as agent_mod

    agent_mod.set_current(agent_mod.detect())

    res = runner.invoke(
        admin_ignore.ignore_app,
        ["remove", "*.tmp", "--path", str(tmp_path)],
    )
    assert res.exit_code == 2, res.output


# ── atomic-write contract ──────────────────────────────────────────────


def test_atomic_write_failure_keeps_original_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If ``Path.replace`` fails mid-write, the original file is untouched.

    Targets the documented atomic-write contract for ``add_pattern``.
    """

    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"], user_lines=["existing.txt"])
    before = target.read_text(encoding="utf-8")

    real_replace = Path.replace

    def boom(self: Path, target: Path) -> Path:
        # Only fail when replacing the .corpusignore itself; let other
        # ``.replace`` calls (e.g. inside Python's atomic_write) succeed.
        if str(target).endswith(".corpusignore"):
            raise OSError("simulated mid-write crash")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", boom, raising=False)

    with pytest.raises(OSError, match="simulated mid-write crash"):
        admin_ignore.add_pattern("*.new", scope="local", path=tmp_path)

    after = target.read_text(encoding="utf-8")
    assert after == before


# ── edit_file (via $EDITOR) ────────────────────────────────────────────


def test_edit_file_rolls_back_on_invalid_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"])
    before = target.read_text(encoding="utf-8")

    # Fake editor: writes a broken regex pattern.
    script = tmp_path / "editor.py"
    script.write_text(
        "import sys, pathlib\npathlib.Path(sys.argv[1]).write_text('[(broken\\n')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        admin_ignore,
        "_resolve_editor",
        lambda: [sys.executable, str(script)],
    )

    rc = admin_ignore.edit_file(scope="local", path=tmp_path)
    assert rc != 0
    # Original content restored from backup.
    after = target.read_text(encoding="utf-8")
    assert after == before


def test_edit_file_keeps_save_on_valid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = _seed_corpusignore(tmp_path, managed_lines=["*.lock"])

    script = tmp_path / "editor.py"
    script.write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "p.write_text(p.read_text() + '\\nuser-edited.txt\\n')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        admin_ignore,
        "_resolve_editor",
        lambda: [sys.executable, str(script)],
    )

    rc = admin_ignore.edit_file(scope="local", path=tmp_path)
    assert rc == 0
    assert "user-edited.txt" in target.read_text(encoding="utf-8")
