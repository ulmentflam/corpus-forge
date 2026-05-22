"""Unit tests for ``corpus_forge.sources._git.git_context``.

Backs RFC ``rfc-source-provenance-git-and-lines`` (P0). The helper is
the foundation that downstream RFC tasks (FilesystemSource wiring,
chunker line numbers, MCP `get_source_file_context`) build on, so the
contract is pinned tight: returns ``(commit, branch)`` for git work
trees, ``(None, None)`` for everything else, never raises.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from corpus_forge.sources._git import git_context


def _git(cwd: Path, *args: str) -> str:
    """Run ``git <args>`` in *cwd* and return stripped stdout.

    Used only by test setup — we trust git to be on PATH here because
    CI always has it. The production helper is the one that has to
    tolerate git's absence.
    """
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(repo: Path) -> str:
    """Initialise a one-commit git repo and return the commit SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    # Set identity locally so `git commit` works on a runner without
    # global config (CI lanes set this globally, but test isolation
    # is worth the extra two lines).
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial commit")
    return _git(repo, "rev-parse", "HEAD")


class TestGitContextHappyPath:
    """Inside a real git work tree, both fields populate."""

    def test_returns_commit_and_branch_for_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        expected_sha = _init_repo(repo)

        commit, branch = git_context(repo)
        assert commit == expected_sha
        assert branch == "main"

    def test_returns_commit_and_branch_for_file_inside_repo(self, tmp_path: Path) -> None:
        """Pointing the helper at a *file* uses the file's parent dir as CWD."""
        repo = tmp_path / "repo-with-file"
        expected_sha = _init_repo(repo)

        nested = repo / "src" / "thing.py"
        nested.parent.mkdir(parents=True)
        nested.write_text("print('hi')\n")

        commit, branch = git_context(nested)
        assert commit == expected_sha
        assert branch == "main"

    def test_returns_commit_and_branch_for_path_that_does_not_exist(self, tmp_path: Path) -> None:
        """A planned-but-not-yet-created file path resolves via its parent."""
        repo = tmp_path / "repo-future-file"
        expected_sha = _init_repo(repo)

        not_yet = repo / "future.py"  # file does not exist
        commit, branch = git_context(not_yet)
        assert commit == expected_sha
        assert branch == "main"

    def test_string_path_accepted(self, tmp_path: Path) -> None:
        """Helper accepts ``str`` as well as ``Path`` (per signature)."""
        repo = tmp_path / "repo-str-arg"
        expected_sha = _init_repo(repo)

        commit, branch = git_context(str(repo))
        assert commit == expected_sha
        assert branch == "main"

    def test_user_path_expanded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A leading ``~`` is expanded against the platform's home env var.

        ``Path.expanduser()`` reads ``$HOME`` on POSIX and ``%USERPROFILE%``
        on Windows; we set both so the test runs identically across all
        three CI lanes (Linux + macOS + Windows). Also strip a few of the
        sibling env vars Windows consults so a leftover real
        ``%USERPROFILE%`` doesn't shadow the override.
        """
        repo = tmp_path / "repo-home"
        expected_sha = _init_repo(repo)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        # Windows' expanduser also consults HOMEDRIVE+HOMEPATH as a
        # fallback. Drop those if set so they can't compete with the
        # USERPROFILE override above.
        monkeypatch.delenv("HOMEDRIVE", raising=False)
        monkeypatch.delenv("HOMEPATH", raising=False)

        commit, branch = git_context("~/repo-home")
        assert commit == expected_sha
        assert branch == "main"


class TestGitContextDetachedHead:
    """Detached HEAD yields a commit but no branch."""

    def test_detached_head_returns_commit_and_none_branch(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo-detached"
        sha = _init_repo(repo)
        # Detach HEAD by checking out the commit directly.
        _git(repo, "checkout", "-q", "--detach", sha)

        commit, branch = git_context(repo)
        assert commit == sha
        assert branch is None, (
            "Detached HEAD must surface as branch=None — the literal "
            "string 'HEAD' from `git rev-parse --abbrev-ref HEAD` is not "
            "a branch name and would mislead downstream provenance"
        )


class TestGitContextFallbacks:
    """Non-git paths and missing tooling return ``(None, None)``."""

    def test_returns_none_none_for_non_git_directory(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "plain-dir"
        not_a_repo.mkdir()
        (not_a_repo / "note.txt").write_text("no git here\n")

        assert git_context(not_a_repo) == (None, None)

    def test_returns_none_none_for_nonexistent_path(self, tmp_path: Path) -> None:
        ghost = tmp_path / "no-such-dir" / "no-such-file"
        assert git_context(ghost) == (None, None)

    def test_returns_none_none_when_git_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If ``git`` isn't on PATH, the helper fails silently."""
        repo = tmp_path / "repo-no-git-bin"
        _init_repo(repo)
        # Wipe PATH so subprocess can't find git; helper must catch
        # FileNotFoundError and return (None, None) instead of raising.
        monkeypatch.setenv("PATH", "")

        assert git_context(repo) == (None, None)

    def test_returns_none_none_when_git_subprocess_raises_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError from subprocess.run is caught (not just FileNotFoundError)."""
        repo = tmp_path / "repo-oserror"
        _init_repo(repo)

        def _raise_oserror(*_args: object, **_kwargs: object) -> object:
            raise OSError("fake permission denied")

        monkeypatch.setattr(subprocess, "run", _raise_oserror)
        assert git_context(repo) == (None, None)

    def test_returns_none_none_on_subprocess_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TimeoutExpired is caught so a hung git call can't block ingest."""
        repo = tmp_path / "repo-timeout"
        _init_repo(repo)

        def _raise_timeout(*_args: object, **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=2)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        assert git_context(repo) == (None, None)

    def test_returns_none_none_when_git_returns_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-zero git exit (e.g. corrupt repo) is treated as 'no provenance'."""
        repo = tmp_path / "repo-nonzero"
        _init_repo(repo)

        class _FakeResult:
            returncode = 128
            stdout = ""
            stderr = "fatal: bad repo"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeResult())
        assert git_context(repo) == (None, None)
