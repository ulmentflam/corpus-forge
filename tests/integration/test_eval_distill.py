"""Q5-T1 RED — ``corpus-forge eval distill`` preprocessing-health metrics tests.

Asserts the contract described in Phase Q Wave 5:

  corpus-forge eval distill --dataset <name> --json

reports preprocessing-health metrics over the SDFT capture set:
  - coverage    : fraction of corpus tokens represented in the SDFT set
  - source_mix  : histogram across all 8 SDFTSource values (zero-fill for absent)
  - template_fidelity : round-trip fidelity stats (n_rows, n_rendered_ok,
                         n_truncated, n_failed)
  - token_stats : p50/p95/max/mean/total over target token counts

Explicitly NOT model-quality metrics.  No judge required.

RED state
---------
``corpus_forge/eval/distill.py`` does not exist and ``eval distill`` is not
registered in ``corpus_forge/cli.py``.  Every test in this file must fail
with either:
  - "No such command 'distill'" (CLI tests), or
  - ``ModuleNotFoundError`` / ``ImportError`` (module-level tests).

Run command::

    uv run pytest tests/integration/test_eval_distill.py -x 2>&1 | tail -20

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.cli import app
from corpus_forge.sdft.capture import record_demonstration

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_RUNNER = CliRunner()

_ALL_SOURCES = [
    "curation_commit",
    "rate_search_result",
    "record_demonstration",
    "cli_feedback",
    "claude_code",
    "gemini",
    "opencode",
    "codex",
]


def _invoke(args: list[str]) -> Result:
    return _RUNNER.invoke(app, args)


def _fresh_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_dataset(backend: SQLiteBackend, name: str = "demo") -> int:
    return backend.get_or_create_dataset(name, "text", "Test dataset for eval distill")


def _seed_chunks(backend: SQLiteBackend, dataset_id: int, n: int = 5) -> list[int]:
    """Insert *n* simple text chunks and return their ids.

    Chunks are joined to the dataset via documents.dataset_id — the chunks
    table does not carry a direct dataset_id column.
    """
    import hashlib

    from corpus_forge.chunkers.base import TextChunk
    from corpus_forge.sources.base import RawDocument

    for i in range(n):
        text = f"Chunk number {i} has some text content for eval testing purposes."
        doc = RawDocument(
            source_uri=f"test://chunk_{i}.txt",
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            text=text,
            title=f"Test Chunk {i}",
            modified_at=0.0,
            metadata={"index": i},
            labels=[],
        )
        chunks = [
            TextChunk(
                text=text,
                heading=f"Heading {i}",
                metadata={"index": i},
                token_count=12,
            )
        ]
        backend.upsert_document(dataset_id, doc, chunks)
    # Chunks link to dataset through document_id → documents.dataset_id.
    rows = backend._execute(
        "SELECT c.id FROM chunks c"
        " JOIN documents d ON d.id = c.document_id"
        " WHERE d.dataset_id = ? ORDER BY c.id",
        (dataset_id,),
    )
    return [int(r["id"]) for r in rows]


def _seed_sdft_rows(
    backend: SQLiteBackend,
    dataset_id: int,
    *,
    n: int = 3,
    source: str = "record_demonstration",
) -> list[int]:
    """Insert *n* SDFT demonstration rows; return their ids."""
    ids: list[int] = []
    with backend._get_connection() as conn:
        for i in range(n):
            result = record_demonstration(
                conn,
                query=f"Query number {i} for source={source}",
                student_messages=[
                    {"role": "assistant", "content": f"Student response {i} before edit."}
                ],
                teacher_messages=[
                    {"role": "assistant", "content": f"Teacher demonstration {i} after edit."}
                ],
                target=f"Improved target text for row {i}, source={source}.",
                source=source,
                dataset_id=dataset_id,
            )
            ids.append(result["demonstration_id"])
    return ids


# ---------------------------------------------------------------------------
# Fixture: in-process backend with seeded data
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_backend(tmp_path: Path) -> tuple[SQLiteBackend, int, str]:
    """Return (backend, dataset_id, dataset_name) with chunks + SDFT rows seeded."""
    backend = _fresh_backend()
    ds_name = "demo"
    ds_id = _seed_dataset(backend, ds_name)
    _seed_chunks(backend, ds_id, n=5)
    _seed_sdft_rows(backend, ds_id, n=3, source="record_demonstration")
    _seed_sdft_rows(backend, ds_id, n=2, source="claude_code")
    return backend, ds_id, ds_name


# ---------------------------------------------------------------------------
# T01 — help exits 0 and lists flags
# ---------------------------------------------------------------------------


class TestEvalDistillHelp:
    def test_help_exits_zero(self) -> None:
        """``corpus-forge eval distill --help`` must exit 0."""
        result = _invoke(["eval", "distill", "--help"])
        assert result.exit_code == 0, (
            f"Expected exit 0 for --help; got {result.exit_code}\n{result.output}"
        )

    def test_help_lists_dataset_flag(self) -> None:
        """Help text must document ``--dataset``."""
        result = _invoke(["eval", "distill", "--help"])
        assert result.exit_code == 0
        assert "--dataset" in result.output, f"--dataset not found in help:\n{result.output}"

    def test_help_lists_json_flag(self) -> None:
        """Help text must document ``--json``."""
        result = _invoke(["eval", "distill", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output, f"--json not found in help:\n{result.output}"

    def test_help_lists_template_flag(self) -> None:
        """Help text must document ``--template`` for fidelity check override."""
        result = _invoke(["eval", "distill", "--help"])
        assert result.exit_code == 0
        assert "--template" in result.output, f"--template not found in help:\n{result.output}"

    def test_help_lists_report_dir_flag(self) -> None:
        """Help text must document ``--report-dir``."""
        result = _invoke(["eval", "distill", "--help"])
        assert result.exit_code == 0
        assert "--report-dir" in result.output, f"--report-dir not found in help:\n{result.output}"


# ---------------------------------------------------------------------------
# T02 — empty SDFT table → coverage=0, all source_mix zeros, n_rows=0
# ---------------------------------------------------------------------------


class TestEvalDistillEmptySet:
    def test_empty_sdft_coverage_is_zero(self, tmp_path: Path) -> None:
        """When the SDFT table is empty, coverage must be 0."""
        backend = _fresh_backend()
        ds_id = _seed_dataset(backend)
        _seed_chunks(backend, ds_id, n=5)
        # No SDFT rows inserted.
        result = _invoke_with_backend(backend, "demo", tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert data["coverage"] == 0, (
            f"Empty SDFT set must yield coverage=0; got {data['coverage']}"
        )

    def test_empty_sdft_source_mix_all_zeros(self, tmp_path: Path) -> None:
        """When the SDFT table is empty, all source_mix counts must be 0."""
        backend = _fresh_backend()
        ds_id = _seed_dataset(backend)
        _seed_chunks(backend, ds_id, n=5)
        result = _invoke_with_backend(backend, "demo", tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        source_mix = data["source_mix"]
        for src, count in source_mix.items():
            assert count == 0, f"source_mix[{src!r}] must be 0 for empty SDFT set; got {count}"

    def test_empty_sdft_n_rows_is_zero(self, tmp_path: Path) -> None:
        """When the SDFT table is empty, template_fidelity.n_rows must be 0."""
        backend = _fresh_backend()
        ds_id = _seed_dataset(backend)
        _seed_chunks(backend, ds_id, n=5)
        result = _invoke_with_backend(backend, "demo", tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert data["template_fidelity"]["n_rows"] == 0, (
            f"n_rows must be 0 for empty SDFT set; got {data['template_fidelity']}"
        )


# ---------------------------------------------------------------------------
# T03 — populated SDFT → coverage > 0, source_mix non-empty
# ---------------------------------------------------------------------------


class TestEvalDistillPopulatedSet:
    def test_populated_sdft_coverage_positive(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """A non-empty SDFT set must yield coverage > 0."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert 0.0 < data["coverage"] <= 1.0, (
            f"Expected 0 < coverage <= 1 for populated SDFT set; got {data['coverage']}"
        )

    def test_populated_sdft_source_mix_non_empty(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """At least one source_mix entry must be > 0 when rows are present."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        source_mix = data["source_mix"]
        assert any(v > 0 for v in source_mix.values()), (
            f"Expected at least one non-zero source_mix entry; got {source_mix}"
        )

    def test_populated_sdft_source_counts_correct(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """source_mix counts must match the number of seeded rows per source."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        source_mix = data["source_mix"]
        # Seeded: 3x record_demonstration, 2x claude_code.
        assert source_mix.get("record_demonstration", 0) == 3, (
            f"Expected source_mix['record_demonstration']=3; got {source_mix}"
        )
        assert source_mix.get("claude_code", 0) == 2, (
            f"Expected source_mix['claude_code']=2; got {source_mix}"
        )


# ---------------------------------------------------------------------------
# T04 — all 8 source values appear as keys in source_mix (zero-fill)
# ---------------------------------------------------------------------------


class TestEvalDistillSourceMixKeys:
    def test_all_8_sources_present_as_keys(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """source_mix must contain all 8 SDFTSource values as keys, even if count=0."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        source_mix = data["source_mix"]
        missing_keys = set(_ALL_SOURCES) - set(source_mix.keys())
        assert not missing_keys, (
            f"source_mix is missing these SDFTSource keys: {sorted(missing_keys)}\n"
            f"Present keys: {sorted(source_mix.keys())}"
        )

    def test_absent_sources_are_zero(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """SDFTSource values with no rows must have count 0 (not missing)."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        source_mix = data["source_mix"]
        # Sources not seeded: curation_commit, rate_search_result, cli_feedback,
        # gemini, opencode, codex.
        absent = [
            "curation_commit",
            "rate_search_result",
            "cli_feedback",
            "gemini",
            "opencode",
            "codex",
        ]
        for src in absent:
            assert source_mix.get(src, -1) == 0, (
                f"Expected source_mix[{src!r}]=0 (not seeded); got {source_mix.get(src)}"
            )


# ---------------------------------------------------------------------------
# T05 — template fidelity: no truncation on small rows
# ---------------------------------------------------------------------------


class TestEvalDistillTemplateFidelity:
    def test_template_fidelity_keys_present(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """template_fidelity must have n_rows, n_rendered_ok, n_truncated, n_failed."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        fidelity = data["template_fidelity"]
        for key in ("n_rows", "n_rendered_ok", "n_truncated", "n_failed"):
            assert key in fidelity, (
                f"template_fidelity missing key {key!r}; got keys {list(fidelity.keys())}"
            )

    def test_template_fidelity_no_truncation_small_rows(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """Small rows must round-trip without truncation: n_truncated = n_failed = 0."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        fidelity = data["template_fidelity"]
        assert fidelity["n_truncated"] == 0, (
            f"Expected n_truncated=0 for small rows; got {fidelity}"
        )
        assert fidelity["n_failed"] == 0, f"Expected n_failed=0 for small rows; got {fidelity}"

    def test_template_fidelity_n_rendered_ok_equals_n_rows(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """For healthy small rows: n_rendered_ok == n_rows."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        fidelity = data["template_fidelity"]
        assert fidelity["n_rendered_ok"] == fidelity["n_rows"], (
            f"Expected n_rendered_ok == n_rows; got {fidelity}"
        )

    def test_template_flag_chatml_override(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """``--template chatml`` must be accepted and fidelity check must complete."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(
            backend, ds_name, tmp_path, json_flag=True, extra_args=["--template", "chatml"]
        )
        assert result.exit_code == 0, f"--template chatml override failed:\n{result.output}"
        data = _extract_json(result.output)
        assert "template_fidelity" in data


# ---------------------------------------------------------------------------
# T06 — token stats ordering invariant
# ---------------------------------------------------------------------------


class TestEvalDistillTokenStats:
    def test_token_stats_keys_present(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """token_stats must have p50, p95, max, mean, total."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        stats = data["token_stats"]
        for key in ("p50", "p95", "max", "mean", "total"):
            assert key in stats, f"token_stats missing key {key!r}; got keys {list(stats.keys())}"

    def test_token_stats_ordering_p50_lte_p95_lte_max(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """p50 <= p95 <= max must hold for any non-empty SDFT set."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        stats = data["token_stats"]
        assert stats["p50"] <= stats["p95"], f"p50 > p95 violates ordering: {stats}"
        assert stats["p95"] <= stats["max"], f"p95 > max violates ordering: {stats}"

    def test_token_stats_total_positive(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """total token count must be > 0 when rows are present."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert data["token_stats"]["total"] > 0, (
            f"Expected total > 0 for populated SDFT set; got {data['token_stats']}"
        )


# ---------------------------------------------------------------------------
# T07 — report directory created
# ---------------------------------------------------------------------------


class TestEvalDistillReportDir:
    def test_report_dir_created_automatically(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """``eval distill`` must create a report directory when it doesn't exist."""
        backend, _ds_id, ds_name = seeded_backend
        report_dir = tmp_path / "new_reports" / "run1"
        assert not report_dir.exists()
        result = _invoke_with_backend(
            backend, ds_name, tmp_path, json_flag=False, report_dir=report_dir
        )
        assert result.exit_code == 0, result.output
        assert report_dir.exists(), f"Report dir was not created: {report_dir}"

    def test_report_dir_contains_markdown(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """Report dir must contain a .md file."""
        backend, _ds_id, ds_name = seeded_backend
        report_dir = tmp_path / "reports"
        result = _invoke_with_backend(
            backend, ds_name, tmp_path, json_flag=False, report_dir=report_dir
        )
        assert result.exit_code == 0, result.output
        md_files = list(report_dir.glob("**/*.md"))
        assert md_files, f"No .md report file found under {report_dir}"

    def test_report_dir_contains_json(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """Report dir must contain a .json file."""
        backend, _ds_id, ds_name = seeded_backend
        report_dir = tmp_path / "reports"
        result = _invoke_with_backend(
            backend, ds_name, tmp_path, json_flag=False, report_dir=report_dir
        )
        assert result.exit_code == 0, result.output
        json_files = list(report_dir.glob("**/*.json"))
        assert json_files, f"No .json report file found under {report_dir}"


# ---------------------------------------------------------------------------
# T08 — deterministic JSON across two runs
# ---------------------------------------------------------------------------


class TestEvalDistillDeterminism:
    def test_deterministic_json_across_two_runs(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """Two consecutive runs on the same SDFT set must produce identical JSON."""
        backend, _ds_id, ds_name = seeded_backend
        result1 = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        result2 = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result1.exit_code == 0, result1.output
        assert result2.exit_code == 0, result2.output
        data1 = _extract_json(result1.output)
        data2 = _extract_json(result2.output)
        # Exclude timestamp fields before comparing.
        for d in (data1, data2):
            d.pop("generated_at", None)
        assert data1 == data2, f"Non-deterministic output:\nRun 1: {data1}\nRun 2: {data2}"


# ---------------------------------------------------------------------------
# T09 — --json emits to stdout only (no markdown file written)
# ---------------------------------------------------------------------------


class TestEvalDistillJsonFlag:
    def test_json_flag_emits_to_stdout(self, seeded_backend: tuple, tmp_path: Path) -> None:
        """``--json`` must emit parseable JSON to stdout."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert isinstance(data, dict), f"Expected dict from JSON output; got {type(data)}"
        # All four metric keys must be present.
        for key in ("coverage", "source_mix", "template_fidelity", "token_stats"):
            assert key in data, f"Expected key {key!r} in JSON output; got {list(data.keys())}"

    def test_json_flag_coverage_in_unit_interval(
        self, seeded_backend: tuple, tmp_path: Path
    ) -> None:
        """coverage must be a float in [0, 1]."""
        backend, _ds_id, ds_name = seeded_backend
        result = _invoke_with_backend(backend, ds_name, tmp_path, json_flag=True)
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert 0.0 <= data["coverage"] <= 1.0, f"coverage {data['coverage']} is outside [0, 1]"


# ---------------------------------------------------------------------------
# T10 — missing dataset → non-zero exit
# ---------------------------------------------------------------------------


class TestEvalDistillMissingDataset:
    def test_missing_dataset_exits_nonzero(self, tmp_path: Path) -> None:
        """Specifying a dataset that does not exist must exit non-zero."""
        result = _invoke(
            [
                "eval",
                "distill",
                "--dataset",
                "__nonexistent_corpus_forge_dataset_xyz123__",
                "--json",
            ]
        )
        assert result.exit_code != 0, (
            f"Expected non-zero exit for missing dataset; got 0\n{result.output}"
        )

    def test_missing_dataset_error_mentions_name(self, tmp_path: Path) -> None:
        """Non-zero exit for missing dataset must mention the dataset name."""
        bad_name = "__nonexistent_corpus_forge_dataset_xyz123__"
        result = _invoke(
            [
                "eval",
                "distill",
                "--dataset",
                bad_name,
                "--json",
            ]
        )
        combined = (result.output or "") + (
            result.stderr if hasattr(result, "stderr") and result.stderr else ""
        )
        assert bad_name in combined or "not found" in combined.lower(), (
            f"Expected dataset name or 'not found' in error output; got:\n{combined}"
        )


# ---------------------------------------------------------------------------
# Internal helpers for invoking via a backend fixture
# ---------------------------------------------------------------------------


def _invoke_with_backend(
    backend: SQLiteBackend,
    dataset: str,
    tmp_path: Path,
    *,
    json_flag: bool,
    extra_args: list[str] | None = None,
    report_dir: Path | None = None,
) -> Result:
    """Invoke ``eval distill`` with an in-memory backend patched in.

    Patches ``corpus_forge.eval.distill._get_backend`` (or the equivalent
    default-backend factory) to return *backend* so the command uses our
    seeded in-memory SQLite DB instead of trying to connect to a real config.
    """
    from unittest.mock import patch

    # The distill command, once implemented, will call something like
    # corpus_forge.eval.distill._build_backend() or similar.  We patch at
    # the module level using the same approach used by test_eval_rag.py.
    # If the implementation delegates to corpus_forge.cli._get_backend we
    # patch that; if it has its own factory we patch that too.
    args: list[str] = ["eval", "distill", "--dataset", dataset]
    if json_flag:
        args.append("--json")
    if report_dir is not None:
        args += ["--report-dir", str(report_dir)]
    else:
        rdir = tmp_path / "reports"
        args += ["--report-dir", str(rdir)]
    if extra_args:
        args.extend(extra_args)

    # Patch each backend-factory target separately with `create=True` so a
    # missing attribute on one symbol doesn't silently fall through and run
    # the unpatched invocation. Real exceptions during the invocation still
    # surface to the caller.
    with (
        patch(
            "corpus_forge.eval.distill._build_backend",
            return_value=backend,
            create=True,
        ),
        patch(
            "corpus_forge.eval.distill._get_backend",
            return_value=backend,
            create=True,
        ),
    ):
        return _RUNNER.invoke(app, args)


def _extract_json(output: str) -> dict:
    """Extract the outer JSON object from CLI output."""
    output = output.strip()
    # Find the FIRST '{' (outer object) and the LAST '}' to bracket the
    # whole top-level dict. Typer may prefix info lines before the JSON.
    start = output.find("{")
    end = output.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"No JSON object found in output:\n{output}")
    return json.loads(output[start:end])
