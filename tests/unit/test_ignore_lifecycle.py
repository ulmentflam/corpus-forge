"""Phase M Wave 1 — managed-block splicer + atomic writer + per-tree
resync helpers.

``corpus_forge.ignore_lifecycle`` provides:

- ``ManagedBlockCorrupted`` exception
- ``splice_managed_block(existing_text, new_block) -> str``
  - preserves text outside the sentinels
  - idempotent on re-splice
  - missing-end sentinel → ``ManagedBlockCorrupted``
- ``atomic_write_text(path, text)`` — tempfile + os.replace
- ``write_corpusignore(root, features) -> CorpusignoreWriteResult``
- ``discover_data_roots(cfg) -> list[Path]``
- ``resync_all(cfg, *, also_global=False) -> list[Path]``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.config import Config
from corpus_forge.ignore_defaults import (
    MANAGED_END,
    MANAGED_START,
    render_managed_block,
)
from corpus_forge.ignore_lifecycle import (
    CorpusignoreWriteResult,
    ManagedBlockCorrupted,
    atomic_write_text,
    discover_data_roots,
    resync_all,
    splice_managed_block,
    write_corpusignore,
)

# ── splice_managed_block ──────────────────────────────────────────────


class TestSpliceManagedBlock:
    def test_empty_existing_inserts_block(self) -> None:
        block = render_managed_block({}, include_timestamp=False)
        out = splice_managed_block("", block)
        assert MANAGED_START in out
        assert MANAGED_END in out

    def test_preserves_user_lines_outside_sentinels(self) -> None:
        block = render_managed_block({}, include_timestamp=False)
        existing = (
            "# user-managed top section\n"
            "MyVault/\n"
            f"{MANAGED_START}\n"
            "old-managed-pattern\n"
            f"{MANAGED_END}\n"
            "# trailing user line\n"
            "OtherTree/\n"
        )
        out = splice_managed_block(existing, block)
        # User lines survive in their original positions.
        assert "MyVault/" in out
        assert "# user-managed top section" in out
        assert "# trailing user line" in out
        assert "OtherTree/" in out
        # And the old managed-block body is gone.
        assert "old-managed-pattern" not in out

    def test_idempotent_on_re_splice(self) -> None:
        block = render_managed_block({"whisper": False}, include_timestamp=False)
        first = splice_managed_block("# top\nMyVault/\n", block)
        second = splice_managed_block(first, block)
        assert first == second

    def test_missing_end_with_start_present_raises(self) -> None:
        block = render_managed_block({}, include_timestamp=False)
        broken = f"# top\n{MANAGED_START}\npat1\npat2\n# no closing sentinel\n"
        with pytest.raises(ManagedBlockCorrupted):
            splice_managed_block(broken, block)

    def test_end_present_but_no_start_treated_as_corrupted(self) -> None:
        block = render_managed_block({}, include_timestamp=False)
        broken = f"# top\npat1\n{MANAGED_END}\nMyVault/\n"
        with pytest.raises(ManagedBlockCorrupted):
            splice_managed_block(broken, block)

    def test_collision_check_requires_exact_full_line_sentinel(self) -> None:
        """A line that merely *contains* the sentinel text as a substring
        must NOT trigger the splicer's recognition — only the exact
        full-line match counts. Guards against a user's comment such as
        ``# see >>> corpus-forge managed in the docs`` accidentally
        being treated as the sentinel.
        """
        block = render_managed_block({}, include_timestamp=False)
        not_a_sentinel = "# this line mentions " + MANAGED_START + " in passing\n"
        # Insert into existing text; splicer should ignore it and append
        # / insert the real block elsewhere.
        existing = f"{not_a_sentinel}MyVault/\n"
        out = splice_managed_block(existing, block)
        # The substring-only line is preserved.
        assert not_a_sentinel.strip() in out
        # And the real sentinel block was inserted exactly once.
        assert out.count(MANAGED_START + "\n") == 1 or out.count(MANAGED_START) == 1


# ── atomic_write_text ─────────────────────────────────────────────────


class TestAtomicWriteText:
    def test_writes_file_with_text(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        atomic_write_text(target, "hello world\n")
        assert target.read_text(encoding="utf-8") == "hello world\n"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        target.write_text("old content", encoding="utf-8")
        atomic_write_text(target, "new content")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_no_partial_file_left_behind_on_success(self, tmp_path: Path) -> None:
        """Implementation contract: tempfile must be cleaned up after
        ``os.replace``. The directory should only contain the final file.
        """
        target = tmp_path / "out.txt"
        atomic_write_text(target, "payload")
        siblings = [p.name for p in tmp_path.iterdir()]
        # No temp turds.
        for name in siblings:
            assert not name.endswith(".tmp")
            assert not name.startswith(".cf-tmp")
        assert "out.txt" in siblings

    def test_parent_directory_must_exist(self, tmp_path: Path) -> None:
        # Caller's responsibility — we don't auto-mkdir to keep the
        # contract narrow. A FileNotFoundError or OSError is acceptable.
        target = tmp_path / "missing-subdir" / "out.txt"
        with pytest.raises((FileNotFoundError, OSError)):
            atomic_write_text(target, "x")


# ── write_corpusignore ────────────────────────────────────────────────


class TestWriteCorpusignore:
    def test_creates_file_on_fresh_root(self, tmp_path: Path) -> None:
        result = write_corpusignore(tmp_path, {"whisper": False})
        assert isinstance(result, CorpusignoreWriteResult)
        path = tmp_path / ".corpusignore"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert MANAGED_START in text
        assert MANAGED_END in text
        # Whisper-off means audio patterns landed inside.
        assert "*.mp4" in text

    def test_round_trip_idempotent_no_user_lines(self, tmp_path: Path) -> None:
        result1 = write_corpusignore(tmp_path, {"whisper": False})
        text1 = (tmp_path / ".corpusignore").read_text(encoding="utf-8")
        result2 = write_corpusignore(tmp_path, {"whisper": False})
        text2 = (tmp_path / ".corpusignore").read_text(encoding="utf-8")

        # Modulo embedded timestamp the body must be identical — but
        # this test passes when timestamps differ as long as the
        # managed block bodies match. Strip from MANAGED_START to
        # MANAGED_END inclusive and compare.
        def _extract_body(text: str) -> str:
            lines = text.splitlines()
            try:
                start = next(i for i, line in enumerate(lines) if line == MANAGED_START)
                end = next(i for i, line in enumerate(lines) if line == MANAGED_END)
            except StopIteration:
                return text
            # Drop the leading timestamp line (first body line is a
            # generated comment) so timestamp diffs don't fail the test.
            return "\n".join(
                line for line in lines[start + 1 : end] if not line.startswith("# Generated ")
            )

        assert _extract_body(text1) == _extract_body(text2)
        # Both results refer to the same path.
        assert result1.path == result2.path == tmp_path / ".corpusignore"

    def test_feature_flip_updates_managed_block_but_keeps_user_lines(self, tmp_path: Path) -> None:
        # First write with whisper off — audio patterns inside.
        write_corpusignore(tmp_path, {"whisper": False})
        path = tmp_path / ".corpusignore"
        # User appends their own pattern below the closing sentinel.
        with path.open("a", encoding="utf-8") as fp:
            fp.write("\n# my own pattern\nMyVault/Drafts/\n")
        # Flip whisper on and rewrite.
        write_corpusignore(tmp_path, {"whisper": True})
        text = path.read_text(encoding="utf-8")
        # User pattern survives.
        assert "MyVault/Drafts/" in text
        # Audio patterns are gone.
        assert "*.mp4" not in text

    def test_corrupted_file_writes_backup_and_rewrites(self, tmp_path: Path) -> None:
        # Half-closed managed block in the existing file.
        path = tmp_path / ".corpusignore"
        path.write_text(
            f"# user header\n{MANAGED_START}\nleftover-pat\n# missing closing sentinel\n",
            encoding="utf-8",
        )
        result = write_corpusignore(tmp_path, {"whisper": False}, backup_corrupted=True)
        # File rewritten to a valid state.
        text = path.read_text(encoding="utf-8")
        assert MANAGED_START in text
        assert MANAGED_END in text
        # Backup exists with the .bak.<ts> naming.
        backups = [p.name for p in tmp_path.iterdir() if p.name.startswith(".corpusignore.bak.")]
        assert backups, "no .corpusignore.bak.<ts> backup left behind"
        # Result signals corruption was handled.
        assert result.corrupted is True
        assert result.backup_path is not None
        assert result.backup_path.exists()

    def test_no_backup_when_backup_corrupted_false(self, tmp_path: Path) -> None:
        path = tmp_path / ".corpusignore"
        path.write_text(
            f"{MANAGED_START}\nleftover\n",
            encoding="utf-8",
        )
        with pytest.raises(ManagedBlockCorrupted):
            write_corpusignore(tmp_path, {"whisper": False}, backup_corrupted=False)


# ── discover_data_roots ───────────────────────────────────────────────


def _make_cfg(sources: list[dict]) -> Config:
    return Config(
        **{
            "backend": {"kind": "sqlite", "dsn": ":memory:"},
            "daemon": {},
            "datasets": [
                {
                    "name": "default",
                    "kind": "text",
                    "sources": sources,
                }
            ],
            "embedders": [
                {
                    "name": "e",
                    "provider": "sentence_transformers",
                    "model_id": "x",
                    "dimension": 8,
                }
            ],
        }
    )


class TestDiscoverDataRoots:
    def test_filesystem_source_yields_root(self, tmp_path: Path) -> None:
        cfg = _make_cfg([{"plugin": "filesystem", "root": str(tmp_path), "chunker": "markdown"}])
        roots = discover_data_roots(cfg)
        assert tmp_path in roots or tmp_path.resolve() in roots

    def test_markdown_vault_source_yields_vault_root(self, tmp_path: Path) -> None:
        cfg = _make_cfg(
            [
                {
                    "plugin": "markdown_vault",
                    "vault_root": str(tmp_path),
                    "chunker": "markdown",
                }
            ]
        )
        roots = discover_data_roots(cfg)
        assert tmp_path in roots or tmp_path.resolve() in roots

    def test_chat_plugins_skipped(self, tmp_path: Path) -> None:
        cfg = _make_cfg(
            [
                {
                    "plugin": "claude_code",
                    "projects_root": str(tmp_path),
                    "chunker": "conversation",
                }
            ]
        )
        # claude_code is not an FS-style ingest of user docs — its
        # projects_root is the daemon's session log dir, not a vault to
        # ignore-pattern. Wave 1 explicitly skips it.
        roots = discover_data_roots(cfg)
        assert tmp_path not in roots
        assert tmp_path.resolve() not in roots

    def test_deduplicates_overlapping_roots(self, tmp_path: Path) -> None:
        cfg = _make_cfg(
            [
                {"plugin": "filesystem", "root": str(tmp_path), "chunker": "markdown"},
                {"plugin": "filesystem", "root": str(tmp_path), "chunker": "markdown"},
            ]
        )
        roots = discover_data_roots(cfg)
        # Two identical sources → one root.
        assert len(roots) == 1


# ── resync_all ────────────────────────────────────────────────────────


class TestResyncAll:
    def test_writes_one_corpusignore_per_root(self, tmp_path: Path) -> None:
        root_a = tmp_path / "tree_a"
        root_b = tmp_path / "tree_b"
        root_a.mkdir()
        root_b.mkdir()
        cfg = _make_cfg(
            [
                {"plugin": "filesystem", "root": str(root_a), "chunker": "markdown"},
                {"plugin": "filesystem", "root": str(root_b), "chunker": "markdown"},
            ]
        )
        written = resync_all(cfg)
        names = {p.name for p in written}
        assert names == {".corpusignore"}
        # Each tree got its own file.
        assert (root_a / ".corpusignore").exists()
        assert (root_b / ".corpusignore").exists()

    def test_also_global_writes_global_file(self, tmp_path: Path, monkeypatch) -> None:
        # Force the global-ignore lookup at a tmp location.
        global_path = tmp_path / "global_ignore"
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(global_path))
        # And a single FS root.
        root_a = tmp_path / "tree_a"
        root_a.mkdir()
        cfg = _make_cfg([{"plugin": "filesystem", "root": str(root_a), "chunker": "markdown"}])
        written = resync_all(cfg, also_global=True)
        # Local + global both written.
        assert (root_a / ".corpusignore") in written
        assert global_path in written
        assert global_path.exists()


# ── 2026-05-27 — dev/build junk flows through the lifecycle ───────────


# The dev/build junk that a fresh ``write_corpusignore`` / ``resync_all``
# must bake in so a code repo under an ingested root doesn't drown the
# scanner. Sampled from the full 21-pattern set.
_JUNK_SAMPLE: tuple[str, ...] = (
    ".git/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    "site-packages/",
)


class TestDevBuildJunkLifecycle:
    def test_fresh_write_includes_junk_patterns(self, tmp_path: Path) -> None:
        write_corpusignore(tmp_path, {"whisper": False})
        text = (tmp_path / ".corpusignore").read_text(encoding="utf-8")
        for pat in _JUNK_SAMPLE:
            assert pat in text, f"junk pattern missing from fresh .corpusignore: {pat!r}"

    def test_resync_writes_junk_into_each_root(self, tmp_path: Path) -> None:
        root_a = tmp_path / "tree_a"
        root_a.mkdir()
        cfg = _make_cfg([{"plugin": "filesystem", "root": str(root_a), "chunker": "markdown"}])
        resync_all(cfg)
        text = (root_a / ".corpusignore").read_text(encoding="utf-8")
        for pat in _JUNK_SAMPLE:
            assert pat in text

    def test_regen_only_rewrites_between_sentinels(self, tmp_path: Path) -> None:
        # User content above AND below the managed block must survive a
        # regen verbatim; only the managed body changes.
        path = tmp_path / ".corpusignore"
        write_corpusignore(tmp_path, {"whisper": False})
        original = path.read_text(encoding="utf-8")
        # Sandwich user lines around the managed block.
        head = "# my custom header\nSecretVault/\n"
        tail = "# trailing notes\nDrafts/private/\n"
        path.write_text(head + original + tail, encoding="utf-8")
        # Regenerate.
        write_corpusignore(tmp_path, {"whisper": False})
        after = path.read_text(encoding="utf-8")
        # User content on both sides survives untouched.
        assert "# my custom header" in after
        assert "SecretVault/" in after
        assert "# trailing notes" in after
        assert "Drafts/private/" in after
        # The managed body still carries the junk patterns.
        for pat in _JUNK_SAMPLE:
            assert pat in after

    def test_regen_is_idempotent_on_junk(self, tmp_path: Path) -> None:
        # Two regens with the same features → identical managed body
        # (modulo the timestamp comment line).
        write_corpusignore(tmp_path, {"whisper": False})
        first = (tmp_path / ".corpusignore").read_text(encoding="utf-8")
        write_corpusignore(tmp_path, {"whisper": False})
        second = (tmp_path / ".corpusignore").read_text(encoding="utf-8")

        def _body(text: str) -> list[str]:
            lines = text.splitlines()
            start = next(i for i, ln in enumerate(lines) if ln == MANAGED_START)
            end = next(i for i, ln in enumerate(lines) if ln == MANAGED_END)
            return [ln for ln in lines[start + 1 : end] if not ln.startswith("# Generated ")]

        assert _body(first) == _body(second)
