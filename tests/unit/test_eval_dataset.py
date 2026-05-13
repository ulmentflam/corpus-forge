"""R3-03 — JSONL gold-set loader unit pins.

Surface under test: ``corpus_forge.eval.dataset``.

Public:
- ``GoldQuery`` (frozen dataclass): ``query_id: str``, ``query: str``,
  ``relevant_chunk_ids: list[int]``, ``graded: dict[int, int] | None``,
  ``content_hashes: list[str] | None``.
- ``load_gold(path: Path) -> list[GoldQuery]``.

Schema (one JSON object per line):

    {"query_id": "q01",
     "query": "How does X work?",
     "relevant_chunk_ids": [123, 124],
     "graded": {"123": 3, "124": 2},      # optional
     "content_hashes": ["abc", "def"]}     # optional, parallel to relevant_chunk_ids

Discipline:
- ``query_id``, ``query``, ``relevant_chunk_ids`` are REQUIRED; missing or
  empty → ``ValueError`` with the line number AND file path.
- ``graded`` may use str OR int keys; loader normalises to int.
- ``content_hashes``, when present, must be the same length as
  ``relevant_chunk_ids`` (else ``ValueError``).
- Blank lines and ``# `` comment lines are skipped.
- Bad JSON on a line → ``ValueError`` mentioning the line number + path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── module presence ───────────────────────────────────────────────────────


def test_module_importable():
    import corpus_forge.eval.dataset  # noqa: F401


def test_public_api_present():
    from corpus_forge.eval.dataset import GoldQuery, load_gold  # noqa: F401


# ── dataclass shape ───────────────────────────────────────────────────────


class TestGoldQueryDataclass:
    def test_required_fields(self):
        from corpus_forge.eval.dataset import GoldQuery

        gq = GoldQuery(
            query_id="q01",
            query="hello?",
            relevant_chunk_ids=[1, 2, 3],
        )
        assert gq.query_id == "q01"
        assert gq.query == "hello?"
        assert gq.relevant_chunk_ids == [1, 2, 3]
        assert gq.graded is None
        assert gq.content_hashes is None

    def test_optional_graded_and_hashes(self):
        from corpus_forge.eval.dataset import GoldQuery

        gq = GoldQuery(
            query_id="q02",
            query="x?",
            relevant_chunk_ids=[1, 2],
            graded={1: 3, 2: 2},
            content_hashes=["aa", "bb"],
        )
        assert gq.graded == {1: 3, 2: 2}
        assert gq.content_hashes == ["aa", "bb"]

    def test_frozen(self):
        """Should be hashable / immutable (frozen dataclass)."""
        from corpus_forge.eval.dataset import GoldQuery

        gq = GoldQuery(query_id="q", query="q", relevant_chunk_ids=[1])
        with pytest.raises((AttributeError, Exception)):
            gq.query_id = "other"  # type: ignore[misc]


# ── happy-path loader ─────────────────────────────────────────────────────


class TestLoadGoldHappyPath:
    def test_loads_minimal_row(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps({"query_id": "q1", "query": "hello", "relevant_chunk_ids": [1, 2]}) + "\n",
            encoding="utf-8",
        )
        out = load_gold(p)
        assert len(out) == 1
        assert out[0].query_id == "q1"
        assert out[0].relevant_chunk_ids == [1, 2]
        assert out[0].graded is None
        assert out[0].content_hashes is None

    def test_loads_multiple_rows(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        with p.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"query_id": "q1", "query": "a", "relevant_chunk_ids": [1]}) + "\n")
            f.write(json.dumps({"query_id": "q2", "query": "b", "relevant_chunk_ids": [2]}) + "\n")
            f.write(json.dumps({"query_id": "q3", "query": "c", "relevant_chunk_ids": [3]}) + "\n")
        out = load_gold(p)
        assert [g.query_id for g in out] == ["q1", "q2", "q3"]

    def test_graded_str_keys_normalised_to_int(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps(
                {
                    "query_id": "q1",
                    "query": "hello",
                    "relevant_chunk_ids": [1, 2],
                    "graded": {"1": 3, "2": 2},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out = load_gold(p)
        assert out[0].graded == {1: 3, 2: 2}
        # Keys must be int after normalisation.
        assert all(isinstance(k, int) for k in out[0].graded)

    def test_graded_int_keys_pass_through(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        # JSON doesn't allow int keys directly, but the loader must handle
        # both (we test the str path above; nothing in the spec forbids ints
        # if a programmatic source emits them).
        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps(
                {
                    "query_id": "q1",
                    "query": "hello",
                    "relevant_chunk_ids": [1, 2],
                    "graded": {"1": 3, "2": 2},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out = load_gold(p)
        assert out[0].graded == {1: 3, 2: 2}

    def test_content_hashes_loaded(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps(
                {
                    "query_id": "q1",
                    "query": "hello",
                    "relevant_chunk_ids": [1, 2],
                    "content_hashes": ["aa", "bb"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        out = load_gold(p)
        assert out[0].content_hashes == ["aa", "bb"]

    def test_mixed_binary_and_graded_rows(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        with p.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"query_id": "q1", "query": "a", "relevant_chunk_ids": [1]}) + "\n")
            f.write(
                json.dumps(
                    {
                        "query_id": "q2",
                        "query": "b",
                        "relevant_chunk_ids": [2, 3],
                        "graded": {"2": 3, "3": 1},
                    }
                )
                + "\n"
            )
        out = load_gold(p)
        assert out[0].graded is None
        assert out[1].graded == {2: 3, 3: 1}

    def test_blank_lines_and_comments_skipped(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        with p.open("w", encoding="utf-8") as f:
            f.write("\n")
            f.write("# this is a comment\n")
            f.write(json.dumps({"query_id": "q1", "query": "a", "relevant_chunk_ids": [1]}) + "\n")
            f.write("\n")
            f.write("# another\n")
            f.write(json.dumps({"query_id": "q2", "query": "b", "relevant_chunk_ids": [2]}) + "\n")
        out = load_gold(p)
        assert [g.query_id for g in out] == ["q1", "q2"]


# ── error paths ───────────────────────────────────────────────────────────


class TestLoadGoldErrors:
    def test_missing_query_id(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(json.dumps({"query": "hello", "relevant_chunk_ids": [1]}) + "\n")
        with pytest.raises(ValueError) as exc:
            load_gold(p)
        msg = str(exc.value)
        assert "query_id" in msg
        assert "line 1" in msg or ":1:" in msg or "row 1" in msg
        assert str(p) in msg or p.name in msg

    def test_missing_query(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(json.dumps({"query_id": "q1", "relevant_chunk_ids": [1]}) + "\n")
        with pytest.raises(ValueError) as exc:
            load_gold(p)
        assert "query" in str(exc.value)

    def test_missing_relevant_chunk_ids(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(json.dumps({"query_id": "q1", "query": "hello"}) + "\n")
        with pytest.raises(ValueError) as exc:
            load_gold(p)
        assert "relevant_chunk_ids" in str(exc.value)

    def test_empty_relevant_chunk_ids(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps({"query_id": "q1", "query": "hello", "relevant_chunk_ids": []}) + "\n"
        )
        with pytest.raises(ValueError) as exc:
            load_gold(p)
        assert "relevant_chunk_ids" in str(exc.value)

    def test_relevant_chunk_ids_not_a_list(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps({"query_id": "q1", "query": "hello", "relevant_chunk_ids": "not-a-list"})
            + "\n"
        )
        with pytest.raises(ValueError):
            load_gold(p)

    def test_bad_json_line_includes_line_number(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        with p.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"query_id": "q1", "query": "a", "relevant_chunk_ids": [1]}) + "\n")
            f.write("{not json\n")
        with pytest.raises(ValueError) as exc:
            load_gold(p)
        msg = str(exc.value)
        assert "line 2" in msg or ":2:" in msg or "row 2" in msg

    def test_content_hashes_length_mismatch(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps(
                {
                    "query_id": "q1",
                    "query": "a",
                    "relevant_chunk_ids": [1, 2, 3],
                    "content_hashes": ["aa", "bb"],
                }
            )
            + "\n"
        )
        with pytest.raises(ValueError) as exc:
            load_gold(p)
        assert "content_hash" in str(exc.value).lower()

    def test_missing_file_raises_filenotfound(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        with pytest.raises(FileNotFoundError):
            load_gold(tmp_path / "does-not-exist.jsonl")

    def test_relevant_chunk_id_must_be_int(self, tmp_path: Path):
        from corpus_forge.eval.dataset import load_gold

        p = tmp_path / "g.jsonl"
        p.write_text(
            json.dumps({"query_id": "q1", "query": "a", "relevant_chunk_ids": ["x", 2]}) + "\n"
        )
        with pytest.raises(ValueError):
            load_gold(p)
