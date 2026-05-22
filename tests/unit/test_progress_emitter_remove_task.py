"""Regression test for PR #46 → ProgressEmitter.remove_task no-op.

PR #46 introduced ``progress.remove_task(source_task)`` in
``ingest_once`` so each per-source bar disappears beneath the
persistent global bar after that source finishes. Rich's
``Progress`` implements ``remove_task``; the agent-mode
``ProgressEmitter`` did not — so ``corpus-forge ingest --once`` under
Claude Code (which forces agent mode via the ``CLAUDE_CODE`` env
var) crashed with ``AttributeError``.

This module pins the no-op contract so the surface doesn\'t silently
drift back. Three assertions:

1. ``ProgressEmitter`` has a ``remove_task`` attribute.
2. Calling it returns ``None`` (no-op).
3. The completed counter is unaffected (so a downstream call site
   that interleaves ``advance`` and ``remove_task`` keeps its state).

If you change ``ProgressEmitter`` to actually track per-task state,
update this test to match the new contract — but keep ``remove_task``
callable so the ingest call site doesn\'t regress.
"""

from __future__ import annotations

from corpus_forge.ui.agent import ProgressEmitter


class TestRemoveTaskNoOp:
    def test_has_remove_task(self) -> None:
        emitter = ProgressEmitter("test", total=100)
        assert hasattr(emitter, "remove_task"), (
            "ProgressEmitter must expose `remove_task` for Rich-API parity. "
            "ingest_once.remove_task(source_task) is called in PR #46\'s "
            "per-source progress bar teardown path."
        )

    def test_remove_task_returns_none(self) -> None:
        emitter = ProgressEmitter("test", total=100)
        task_id = emitter.add_task("source-A", total=50)
        assert emitter.remove_task(task_id) is None

    def test_remove_task_preserves_completed_counter(self) -> None:
        """``remove_task`` is a no-op, so advancing then removing then
        advancing again accumulates as if the remove never happened."""
        emitter = ProgressEmitter("test", total=100)
        task_id = emitter.add_task("source-A", total=50)
        emitter.advance(task_id, n=10)
        assert emitter._completed == 10
        emitter.remove_task(task_id)
        assert emitter._completed == 10, "remove_task must not reset counter"
        emitter.advance(task_id, n=5)
        assert emitter._completed == 15
