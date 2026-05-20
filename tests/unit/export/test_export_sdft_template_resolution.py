"""Q4-T1 RED — Template resolution tests for export_sdft.

Verifies that the ``template`` argument to ``export_sdft`` resolves through
the same path as ``export_chat`` (model_id / custom_jinja / registered builtin),
matching the behaviour documented in ``corpus_forge/templates/__init__.py``.

RED state
---------
``corpus_forge.export.export_sdft`` does not exist yet.  Every test fails::

    ImportError: cannot import name 'export_sdft' from 'corpus_forge.export'

Run command::

    uv run pytest tests/unit/export/test_export_sdft_template_resolution.py -x 2>&1 | tail -30
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Import the target function — will fail ImportError → RED
# ---------------------------------------------------------------------------
from corpus_forge.export import export_sdft  # type: ignore[attr-defined]

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _insert_dataset(backend: SQLiteBackend, name: str = "q4-tmpl-ds") -> int:
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (name, "chat", "Template resolution test dataset"),
        ).fetchone()[0]
        conn.commit()
    return ds_id


def _insert_sdft_row(
    backend: SQLiteBackend,
    dataset_id: int,
    *,
    query: str = "Resolve this.",
    source: str = "cli_feedback",
) -> None:
    import hashlib as _hashlib
    import json as _json

    student_messages = [{"role": "assistant", "content": "My answer"}]
    teacher_messages = [{"role": "user", "content": "Correct answer"}]
    target = "The right answer."

    student_json = _json.dumps(student_messages)
    teacher_json = _json.dumps(teacher_messages)
    payload = _json.dumps(
        [query, student_messages, teacher_messages, target],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    content_hash = _hashlib.sha256(payload.encode("utf-8")).hexdigest()

    with backend._get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sdft_demonstrations
              (dataset_id, query, student_messages, teacher_messages,
               target, source, trace_id, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (dataset_id, query, student_json, teacher_json, target, source, None, content_hash),
        )
        conn.commit()


def _read_jsonl(path: Path) -> list[dict]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# ===========================================================================
# Builtin template names
# ===========================================================================


class TestExportSdftBuiltinTemplates:
    def test_chatml_template_name_resolves(self, tmp_path: Path) -> None:
        """template='chatml' resolves via builtins without error."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft_chatml.jsonl"

        # Must not raise; chatml is a known builtin
        result = export_sdft("q4-tmpl-ds", "chatml", out, format="jsonl", backend=backend)
        assert result["row_count"] == 1

    def test_qwen_template_name_resolves(self, tmp_path: Path) -> None:
        """template='qwen' resolves via builtins without error."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft_qwen.jsonl"

        result = export_sdft("q4-tmpl-ds", "qwen", out, format="jsonl", backend=backend)
        assert result["row_count"] == 1

    def test_llama3_template_name_resolves(self, tmp_path: Path) -> None:
        """template='llama3' resolves via builtins without error."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft_llama3.jsonl"

        result = export_sdft("q4-tmpl-ds", "llama3", out, format="jsonl", backend=backend)
        assert result["row_count"] == 1


# ===========================================================================
# Custom Jinja2 template
# ===========================================================================


class TestExportSdftCustomJinja:
    def test_custom_jinja_string_is_accepted(self, tmp_path: Path) -> None:
        """A Jinja2 template string passed as custom_jinja renders without error."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft_jinja.jsonl"
        custom = "RENDER:{{ messages | length }}"

        result = export_sdft(
            "q4-tmpl-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            custom_jinja=custom,
        )

        assert result["row_count"] == 1

    def test_custom_jinja_overrides_builtin(self, tmp_path: Path) -> None:
        """When custom_jinja is set, the builtin template is NOT used."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft_jinja_override.jsonl"

        # Pass a Jinja template that produces a unique marker in rendered text
        custom = "CUSTOM_MARKER_XYZ:{{ messages | length }}"

        result = export_sdft(
            "q4-tmpl-ds",
            "chatml",
            out,
            format="jsonl",
            backend=backend,
            custom_jinja=custom,
        )

        # Row was written, template field reflects what was passed
        assert result["row_count"] == 1
        rows = _read_jsonl(out)
        # The template field in the row must reflect the call argument
        assert rows[0]["template"] is not None


# ===========================================================================
# Unknown template name
# ===========================================================================


class TestExportSdftUnknownTemplate:
    def test_unknown_template_name_raises_error(self, tmp_path: Path) -> None:
        """An unknown template name that is not a builtin raises KeyError or ValueError."""
        backend = _make_backend()
        ds_id = _insert_dataset(backend)
        _insert_sdft_row(backend, ds_id)
        out = tmp_path / "sdft_bad_tmpl.jsonl"

        with pytest.raises((KeyError, ValueError)):
            export_sdft(
                "q4-tmpl-ds",
                "this_template_does_not_exist_xyz",
                out,
                format="jsonl",
                backend=backend,
            )
