"""Phase I-03 — Schema invariants for ``packaging/install/questions.toml``.

The TOML question tree is consumed by three shell installers + the
Python ``corpus-forge setup`` wizard. Drift in field names or value
shapes silently breaks one consumer or another, so this test pins the
schema invariants explicitly.

Tests run on every OS in the matrix — including Windows — to catch
TOML-parse / path-handling regressions early.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

QUESTIONS_PATH = Path(__file__).resolve().parents[2] / "packaging" / "install" / "questions.toml"

# Allowed values for the ``type`` field. Adding a new shape needs all
# three shells + the Python wizard to learn it, so keep the list small.
_ALLOWED_TYPES = frozenset({"yes_no", "choice", "text"})

# Allowed pip extras — must match the [project.optional-dependencies]
# block in pyproject.toml. Keep in sync if new extras are added.
_KNOWN_EXTRAS = frozenset(
    {
        "openai",
        "hf",
        "tokens",
        "sqlite",
        "retrieval",
        "eval",
        "rerank",
        "mcp",
        "code",
        "multi-format",
        "ocr",
        "whisper",
    }
)


@pytest.fixture(scope="module")
def questions() -> list[dict]:
    """Parse questions.toml once for the whole test module."""
    with QUESTIONS_PATH.open("rb") as f:
        data = tomllib.load(f)
    assert "question" in data, "questions.toml must declare at least one [[question]]"
    return data["question"]


# ── per-question invariants ────────────────────────────────────────────


class TestPerQuestion:
    def test_every_question_has_required_fields(self, questions: list[dict]) -> None:
        """``id``, ``prompt``, ``type``, ``env`` — the four every question carries."""
        required = ("id", "prompt", "type", "env")
        for q in questions:
            for field in required:
                assert field in q, f"Question {q.get('id', '<no id>')} missing {field!r}"

    def test_type_is_allowed(self, questions: list[dict]) -> None:
        for q in questions:
            assert q["type"] in _ALLOWED_TYPES, (
                f"Question {q['id']} type={q['type']!r} not in {_ALLOWED_TYPES}"
            )

    def test_choice_questions_carry_choices(self, questions: list[dict]) -> None:
        for q in questions:
            if q["type"] == "choice":
                assert "choices" in q and len(q["choices"]) >= 2, (
                    f"Choice question {q['id']} needs at least 2 choices"
                )
                assert q.get("default") in q["choices"], (
                    f"Choice question {q['id']} default={q.get('default')!r} "
                    f"not in choices {q['choices']}"
                )

    def test_yes_no_default_is_yes_or_no(self, questions: list[dict]) -> None:
        for q in questions:
            if q["type"] == "yes_no":
                assert q.get("default") in ("yes", "no"), (
                    f"Yes/no question {q['id']} default must be 'yes' or 'no'"
                )

    def test_env_var_names_are_valid_posix(self, questions: list[dict]) -> None:
        """Env vars must be valid POSIX identifiers (re-uses the same
        regex as :func:`corpus_forge.config._validate_env_var_name`)."""
        import re

        valid = re.compile(r"^[A-Z][A-Z0-9_]*$")
        for q in questions:
            assert valid.match(q["env"]), (
                f"Question {q['id']} env={q['env']!r} is not a valid "
                "POSIX env var name (uppercase A-Z, digits, underscore)"
            )

    def test_env_vars_are_unique(self, questions: list[dict]) -> None:
        seen: dict[str, str] = {}
        for q in questions:
            env = q["env"]
            assert env not in seen, f"Duplicate env var {env!r} on {q['id']} and {seen[env]}"
            seen[env] = q["id"]

    def test_ids_are_unique(self, questions: list[dict]) -> None:
        ids = [q["id"] for q in questions]
        assert len(ids) == len(set(ids)), f"Duplicate question ids: {ids}"

    def test_extras_are_known(self, questions: list[dict]) -> None:
        for q in questions:
            for extra in q.get("extras", []):
                assert extra in _KNOWN_EXTRAS, (
                    f"Question {q['id']} declares unknown extra {extra!r}. "
                    f"Add it to pyproject.toml's [project.optional-dependencies] "
                    "or fix the typo."
                )


# ── dependency graph invariants ────────────────────────────────────────


class TestDependsOn:
    def test_depends_on_references_known_question(self, questions: list[dict]) -> None:
        """``depends_on = "<id>=<value>"`` must reference a real question."""
        ids = {q["id"] for q in questions}
        for q in questions:
            dep = q.get("depends_on")
            if not dep:
                continue
            assert "=" in dep, f"Question {q['id']} depends_on={dep!r} must be 'id=value'"
            target, _, _ = dep.partition("=")
            assert target in ids, f"Question {q['id']} depends_on references unknown id {target!r}"

    def test_depends_on_value_is_valid_for_target(self, questions: list[dict]) -> None:
        """``depends_on = "foo=bar"`` requires ``bar`` to be a valid answer
        for question ``foo``. For choice/yes_no questions we can check;
        text questions are skipped."""
        by_id = {q["id"]: q for q in questions}
        for q in questions:
            dep = q.get("depends_on")
            if not dep:
                continue
            target, _, value = dep.partition("=")
            target_q = by_id[target]
            if target_q["type"] == "choice":
                assert value in target_q["choices"], (
                    f"Question {q['id']} depends on {target}={value!r} "
                    f"but {target}'s choices are {target_q['choices']}"
                )
            elif target_q["type"] == "yes_no":
                assert value in ("yes", "no"), (
                    f"Question {q['id']} depends on {target}={value!r} "
                    f"but {target} is yes/no — value must be 'yes' or 'no'"
                )


# ── coverage of the 15 surfaces from the Phase I audit ────────────────


class TestSurfaceCoverage:
    """Phase I-03's DoD: the question tree must prompt for every
    user-facing surface the audit listed."""

    def test_15_surfaces_are_prompted(self, questions: list[dict]) -> None:
        # One id per surface from the audit. If a surface is renamed,
        # update this list so the check stays meaningful.
        required_ids = {
            "backend",  # storage backend
            "multi_format",  # multi-format ingest
            "code_ingest",  # code-aware ingest
            "ocr_escalation",  # OCR escalation
            "whisper_transcription",  # audio/video transcription
            "tokens",  # token-aware chunking
            "retrieval",  # retrieval + eval
            "reranker",  # cross-encoder reranker
            "embedder",  # text embedder choice
            "classifier_chain",  # classifier chain
            "vlm_backend",  # VLM backend (when ocr=yes)
            "whisper_backend",  # whisper backend (when whisper=yes)
            "code_enricher",  # code enricher backend (when code=yes)
            "mcp_server",  # MCP server
            "hf_export",  # HF export
            "daemon_supervisor",  # daemon supervisor registration
        }
        ids = {q["id"] for q in questions}
        missing = required_ids - ids
        assert not missing, f"Question tree missing required surfaces: {sorted(missing)}"
