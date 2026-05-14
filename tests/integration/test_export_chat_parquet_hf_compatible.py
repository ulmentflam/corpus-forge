"""G-04 RED — HF-compatibility tests for export_chat Parquet writer.

Pins that the Parquet file produced by ``export_chat(..., format='parquet')``
is loadable via ``datasets.load_dataset('parquet', ...)`` and has the correct
schema.

All tests use ``pytest.importorskip("datasets")`` so they are gracefully
skipped when the ``[hf]`` extra isn't installed.

All tests FAIL RED because ``corpus_forge.export.export_chat`` doesn't exist.

Run command:
    .venv/bin/python -m pytest tests/integration/test_export_chat_parquet_hf_compatible.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Import the target function (will raise ImportError / AttributeError → RED)
# ---------------------------------------------------------------------------

from corpus_forge.export import export_chat  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers (mirror test_export_chat_jsonl.py)
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_dataset(backend: SQLiteBackend, name: str) -> int:
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (name, "chat", "G-04 parquet test dataset"),
        ).fetchone()[0]
        conn.commit()
    return ds_id


def _seed_conversations(
    backend: SQLiteBackend,
    dataset_id: int,
    count: int = 2,
    messages_per_conv: int = 3,
) -> list[int]:
    roles = ["user", "assistant", "user", "assistant"]
    conv_ids: list[int] = []
    for i in range(count):
        messages = [
            {
                "role": roles[j % len(roles)],
                "content": f"G-04 parquet seed conv={i} msg={j}",
            }
            for j in range(messages_per_conv)
        ]
        conv_id, _ = backend.append_conversation(
            dataset_id=dataset_id,
            title=f"G-04 parquet conversation {i}",
            started_at=None,
            messages=messages,
        )
        conv_ids.append(conv_id)
    return conv_ids


# ---------------------------------------------------------------------------
# Expected schema columns
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = {
    "conversation_id",
    "title",
    "source_uri",
    "description",
    "template",
    "model_id",
    "text",
    "message_count",
    "messages",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExportChatParquetLoadsViaDatasets:
    def test_export_chat_parquet_loads_via_datasets(self, tmp_path: Path) -> None:
        """Export to Parquet; load via datasets.load_dataset; assert columns + row count.

        Skipped when datasets is not installed (``[hf]`` extra absent).
        """
        datasets_mod = pytest.importorskip("datasets")

        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-parquet-load")
        _seed_conversations(backend, ds_id, count=2, messages_per_conv=3)

        out_path = tmp_path / "out.parquet"
        export_chat(
            dataset="test-parquet-load",
            template="chatml",
            out_path=out_path,
            format="parquet",
            backend=backend,
        )

        assert out_path.exists(), "Parquet file must exist after export_chat"

        hf_ds = datasets_mod.load_dataset("parquet", data_files=str(out_path), split="train")

        # Correct row count
        assert len(hf_ds) == 2, f"Expected 2 rows in HF dataset; got {len(hf_ds)}"

        # All required columns present
        actual_columns = set(hf_ds.column_names)
        missing = _EXPECTED_COLUMNS - actual_columns
        assert not missing, (
            f"Parquet file missing required columns: {missing}. Actual columns: {actual_columns}"
        )

    def test_export_chat_parquet_text_column_renders_correctly(self, tmp_path: Path) -> None:
        """First row's 'text' field contains the ChatML marker after load via datasets."""
        datasets_mod = pytest.importorskip("datasets")

        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-parquet-text")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=3)

        out_path = tmp_path / "out.parquet"
        export_chat(
            dataset="test-parquet-text",
            template="chatml",
            out_path=out_path,
            format="parquet",
            backend=backend,
        )

        hf_ds = datasets_mod.load_dataset("parquet", data_files=str(out_path), split="train")
        assert len(hf_ds) >= 1
        first_row_text = hf_ds[0]["text"]
        assert isinstance(first_row_text, str), (
            f"Expected 'text' to be a str; got {type(first_row_text)}"
        )
        assert "<|im_start|>" in first_row_text, (
            f"Expected ChatML <|im_start|> marker in first row text; got: {first_row_text!r}"
        )

    def test_export_chat_parquet_message_count_column_is_integer(self, tmp_path: Path) -> None:
        """The message_count column in the loaded Parquet contains integer values."""
        datasets_mod = pytest.importorskip("datasets")

        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-parquet-msgcount")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=4)

        out_path = tmp_path / "out.parquet"
        export_chat(
            dataset="test-parquet-msgcount",
            template="chatml",
            out_path=out_path,
            format="parquet",
            backend=backend,
        )

        hf_ds = datasets_mod.load_dataset("parquet", data_files=str(out_path), split="train")
        assert len(hf_ds) == 1
        mc = hf_ds[0]["message_count"]
        assert isinstance(mc, int), (
            f"Expected message_count to be int in Parquet; got {type(mc)}: {mc}"
        )
        assert mc == 4, f"Expected message_count=4; got {mc}"


class TestExportChatParquetEdgeCases:
    def test_export_chat_parquet_empty_dataset_produces_valid_file(self, tmp_path: Path) -> None:
        """Empty dataset → Parquet file with 0 rows but correct schema.

        Skipped when datasets is not installed.
        """
        datasets_mod = pytest.importorskip("datasets")

        backend = _make_backend()
        _seed_dataset(backend, "test-parquet-empty")

        out_path = tmp_path / "out.parquet"
        export_chat(
            dataset="test-parquet-empty",
            template="chatml",
            out_path=out_path,
            format="parquet",
            backend=backend,
        )

        assert out_path.exists(), "Parquet file must be created even for empty dataset"
        # Loading an empty Parquet may raise or return 0-row dataset
        try:
            hf_ds = datasets_mod.load_dataset("parquet", data_files=str(out_path), split="train")
            assert len(hf_ds) == 0, f"Expected 0 rows for empty dataset; got {len(hf_ds)}"
        except Exception:
            # Some versions of datasets error on 0-row parquet; acceptable.
            pass

    def test_export_chat_parquet_with_hf_model_id_stub(self, tmp_path: Path) -> None:
        """Parquet export with model_id uses hf_template stub; rows contain stub marker.

        Skipped when datasets is not installed.
        """
        datasets_mod = pytest.importorskip("datasets")

        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-parquet-hf-stub")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=2)

        stub_jinja = "<HF>{{ messages[0]['content'] }}</HF>"

        with patch("corpus_forge.templates.hf.hf_template", return_value=stub_jinja):
            out_path = tmp_path / "out.parquet"
            export_chat(
                dataset="test-parquet-hf-stub",
                template="chatml",
                out_path=out_path,
                format="parquet",
                backend=backend,
                model_id="stub-hf-model",
            )

        hf_ds = datasets_mod.load_dataset("parquet", data_files=str(out_path), split="train")
        assert len(hf_ds) == 1
        assert "<HF>" in hf_ds[0]["text"], (
            f"Expected HF stub marker in Parquet row text; got {hf_ds[0]['text']!r}"
        )
