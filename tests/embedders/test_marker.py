"""Phase L Wave 5 — pending/skipped re-embed marker file behaviour.

The marker file is the bridge between CLI invocations. ``ingest`` /
``embed`` see drift, prompt the user, and call ``mark_pending`` (later)
or ``mark_skipped`` (skip + 7-day suppression). The next invocation
calls ``check_pending_or_skipped`` to decide whether to re-prompt or
respect the user's prior choice.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """Redirect the marker file under ``tmp_path`` so tests don't leak."""

    import corpus_forge.embedders._marker as marker_mod

    state_dir = tmp_path / "cf-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        marker_mod,
        "_state_dir",
        lambda: state_dir,
    )
    return state_dir


def test_mark_pending_writes_json():
    """``mark_pending`` records the entry as pending."""

    from corpus_forge.embedders._marker import check_pending_or_skipped, mark_pending

    mark_pending("e1", fp_was="a" * 16, fp_now="b" * 16)

    assert check_pending_or_skipped("e1", "b" * 16) == "pending"


def test_mark_skipped_with_ttl(monkeypatch):
    """``mark_skipped`` enforces a 7-day suppression window."""

    from corpus_forge.embedders import _marker as marker_mod
    from corpus_forge.embedders._marker import (
        check_pending_or_skipped,
        mark_skipped,
    )

    mark_skipped("e1", fp_was="a" * 16, fp_now="b" * 16)

    # Within the window → skipped.
    assert check_pending_or_skipped("e1", "b" * 16) == "skipped"

    # Advance the clock 8 days → no longer suppressed.
    real_dt = marker_mod.datetime

    class _FrozenDT(real_dt):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz=None):
            return real_dt.now(tz) + timedelta(days=8)

    monkeypatch.setattr(marker_mod, "datetime", _FrozenDT)

    assert check_pending_or_skipped("e1", "b" * 16) == "none"


def test_check_returns_none_on_unknown():
    """Unknown entry → ``none``."""

    from corpus_forge.embedders._marker import check_pending_or_skipped

    assert check_pending_or_skipped("nonexistent", "b" * 16) == "none"


def test_marker_re_change_fingerprint_invalidates_skip():
    """If the user changed fingerprints again, the suppression doesn't apply."""

    from corpus_forge.embedders._marker import check_pending_or_skipped, mark_skipped

    mark_skipped("e1", fp_was="a" * 16, fp_now="b" * 16)

    # User flipped the model again — now their fp_now is "c".
    assert check_pending_or_skipped("e1", "c" * 16) == "none"


def test_clear_marker_removes_entry():
    """``clear_marker`` deletes the entry; subsequent checks see ``none``."""

    from corpus_forge.embedders._marker import (
        check_pending_or_skipped,
        clear_marker,
        mark_pending,
    )

    mark_pending("e1", fp_was="a" * 16, fp_now="b" * 16)
    assert check_pending_or_skipped("e1", "b" * 16) == "pending"

    clear_marker("e1")

    assert check_pending_or_skipped("e1", "b" * 16) == "none"


def test_atomic_write_doesnt_race():
    """Concurrent writers don't tear the JSON file.

    The atomic-rename guarantees readers always see *some* valid JSON
    payload mid-flight (never a partial half-written file).  Read-
    modify-write is not lock-protected, so the final file's entry set
    is "last writer wins per key" — we don't assert all-writers-present.
    """

    from corpus_forge.embedders._marker import _marker_path, mark_pending

    errors: list[BaseException] = []
    read_errors: list[BaseException] = []

    def _writer(idx: int):
        try:
            for _ in range(20):
                mark_pending(f"e{idx}", fp_was="a" * 16, fp_now=f"{idx}" * 16)
        except BaseException as exc:
            errors.append(exc)

    def _reader():
        try:
            for _ in range(50):
                if _marker_path().exists():
                    text = _marker_path().read_text(encoding="utf-8")
                    if text.strip():
                        json.loads(text)  # must always parse
        except BaseException as exc:
            read_errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
    threads.append(threading.Thread(target=_reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"writer thread errors: {errors!r}"
    assert not read_errors, f"reader saw torn JSON: {read_errors!r}"

    # Final file is well-formed JSON.
    data = json.loads(_marker_path().read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    # At least one writer's entry survived.
    assert any(f"e{i}" in data for i in range(4))


def test_pending_marker_carries_fp_pair():
    """The serialized entry preserves fp_was + fp_now for the drift-flow handler."""

    from corpus_forge.embedders._marker import _marker_path, mark_pending

    mark_pending("e1", fp_was="aaaaaaaaaaaaaaaa", fp_now="bbbbbbbbbbbbbbbb")

    data = json.loads(_marker_path().read_text(encoding="utf-8"))
    entry = data["e1"]
    assert entry["state"] == "pending"
    assert entry["fp_was"] == "aaaaaaaaaaaaaaaa"
    assert entry["fp_now"] == "bbbbbbbbbbbbbbbb"
    assert entry["detected_at"]


def test_skipped_marker_carries_suppression_window():
    """``mark_skipped`` writes a ``suppressed_until`` ~7 days in the future."""

    from corpus_forge.embedders._marker import _marker_path, mark_skipped

    mark_skipped("e1", fp_was="a" * 16, fp_now="b" * 16)

    data = json.loads(_marker_path().read_text(encoding="utf-8"))
    entry = data["e1"]
    assert entry["state"] == "skipped"
    suppressed = datetime.fromisoformat(entry["suppressed_until"])
    now = datetime.now(UTC)
    # 7 days, with up to a 60-second wall clock fudge.
    delta = suppressed - now
    fudge = timedelta(minutes=1)
    assert timedelta(days=7) - fudge <= delta <= timedelta(days=7) + fudge
