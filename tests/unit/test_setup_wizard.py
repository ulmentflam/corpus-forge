"""Phase I-07/08 — unit tests for the corpus-forge setup wizard.

Covers:

- :func:`load_questions` parses ``packaging/install/questions.toml``
  and yields ordered :class:`Question` objects.
- :func:`render_config_toml` produces a parseable TOML block for every
  combination of backend / embedder / VLM / Whisper / classifier /
  code-enricher selectors.
- The ``depends_on`` predicate skips irrelevant prompts.
- Non-interactive mode reads ``CF_*`` env vars and falls back to
  question defaults when the env var is unset.
- Interactive mode honours user input via stream injection.
"""

from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest

from corpus_forge.setup import (
    Question,
    load_questions,
    render_config_toml,
    run_non_interactive,
    run_wizard,
)
from corpus_forge.setup.wizard import (
    _collect_answers,
    _normalise_yes_no,
    _read_answer_interactive,
    _read_answer_non_interactive,
    render_secrets_env,
)

# ── Question parsing ──────────────────────────────────────────────────


class TestLoadQuestions:
    def test_load_default_questions(self) -> None:
        questions = load_questions()
        assert len(questions) >= 15
        assert all(isinstance(q, Question) for q in questions)
        assert questions[0].id == "backend"

    def test_question_fields_round_trip(self) -> None:
        questions = load_questions()
        backend_q = next(q for q in questions if q.id == "backend")
        assert backend_q.type == "choice"
        assert backend_q.default == "sqlite"
        assert backend_q.choices == ["sqlite", "postgres"]
        assert backend_q.env == "CF_BACKEND"


# ── depends_on ────────────────────────────────────────────────────────


class TestDependsOn:
    def test_no_dep_always_relevant(self) -> None:
        q = Question(id="x", prompt="?", type="yes_no", env="CF_X")
        assert q.is_relevant({}) is True

    def test_dep_satisfied(self) -> None:
        q = Question(
            id="x",
            prompt="?",
            type="yes_no",
            env="CF_X",
            depends_on="backend=sqlite",
        )
        assert q.is_relevant({"backend": "sqlite"}) is True

    def test_dep_unsatisfied(self) -> None:
        q = Question(
            id="x",
            prompt="?",
            type="yes_no",
            env="CF_X",
            depends_on="backend=postgres",
        )
        assert q.is_relevant({"backend": "sqlite"}) is False


# ── yes/no normalisation ──────────────────────────────────────────────


class TestNormaliseYesNo:
    @pytest.mark.parametrize("raw", ["y", "Y", "yes", "Yes", "YES"])
    def test_yes_variants(self, raw: str) -> None:
        assert _normalise_yes_no(raw, default="no") == "yes"

    @pytest.mark.parametrize("raw", ["n", "N", "no", "No", "NO"])
    def test_no_variants(self, raw: str) -> None:
        assert _normalise_yes_no(raw, default="yes") == "no"

    def test_empty_returns_default(self) -> None:
        assert _normalise_yes_no("", default="yes") == "yes"
        assert _normalise_yes_no("", default="no") == "no"

    def test_garbage_returns_none(self) -> None:
        assert _normalise_yes_no("maybe", default="yes") is None


# ── Non-interactive reader ────────────────────────────────────────────


class TestReadAnswerNonInteractive:
    def test_env_var_present_returns_value(self) -> None:
        q = Question(
            id="backend",
            prompt="?",
            type="choice",
            env="CF_BACKEND",
            choices=["sqlite", "postgres"],
            default="sqlite",
        )
        assert _read_answer_non_interactive(q, {"CF_BACKEND": "postgres"}) == "postgres"

    def test_env_var_missing_falls_back_to_default(self) -> None:
        q = Question(
            id="backend",
            prompt="?",
            type="choice",
            env="CF_BACKEND",
            choices=["sqlite", "postgres"],
            default="sqlite",
        )
        assert _read_answer_non_interactive(q, {}) == "sqlite"

    def test_yes_no_yes(self) -> None:
        q = Question(id="mcp", prompt="?", type="yes_no", env="CF_MCP", default="no")
        assert _read_answer_non_interactive(q, {"CF_MCP": "y"}) == "yes"

    def test_yes_no_empty_uses_default(self) -> None:
        q = Question(id="mcp", prompt="?", type="yes_no", env="CF_MCP", default="yes")
        assert _read_answer_non_interactive(q, {"CF_MCP": ""}) == "yes"

    def test_choice_invalid_falls_back(self) -> None:
        q = Question(id="b", prompt="?", type="choice", env="CF_B", choices=["a", "b"], default="a")
        assert _read_answer_non_interactive(q, {"CF_B": "garbage"}) == "a"


# ── Interactive reader (stream-injected) ──────────────────────────────


class TestReadAnswerInteractive:
    def test_returns_user_input(self) -> None:
        q = Question(id="b", prompt="?", type="choice", env="CF_B", choices=["a", "b"], default="a")
        out = io.StringIO()
        ans = _read_answer_interactive(q, stream_in=io.StringIO("b\n"), stream_out=out)
        assert ans == "b"

    def test_empty_input_uses_default(self) -> None:
        q = Question(id="b", prompt="?", type="text", env="CF_B", default="hello")
        out = io.StringIO()
        ans = _read_answer_interactive(q, stream_in=io.StringIO("\n"), stream_out=out)
        assert ans == "hello"

    def test_yes_no_normalises(self) -> None:
        q = Question(id="mcp", prompt="?", type="yes_no", env="CF_MCP", default="yes")
        out = io.StringIO()
        ans = _read_answer_interactive(q, stream_in=io.StringIO("Y\n"), stream_out=out)
        assert ans == "yes"

    def test_invalid_yes_no_re_prompts(self) -> None:
        q = Question(id="mcp", prompt="?", type="yes_no", env="CF_MCP", default="no")
        out = io.StringIO()
        ans = _read_answer_interactive(q, stream_in=io.StringIO("maybe\nyes\n"), stream_out=out)
        assert ans == "yes"
        assert "answer y or n" in out.getvalue()

    def test_warn_emitted(self) -> None:
        q = Question(
            id="m", prompt="?", type="yes_no", env="CF_M", default="no", warn="heads up — AGPL"
        )
        out = io.StringIO()
        _read_answer_interactive(q, stream_in=io.StringIO("n\n"), stream_out=out)
        assert "heads up — AGPL" in out.getvalue()


# ── render_config_toml ────────────────────────────────────────────────


class TestRenderConfigToml:
    def _parse(self, text: str) -> dict:
        return tomllib.loads(text)

    def test_sqlite_default(self, tmp_path: Path) -> None:
        answers = {"backend": "sqlite", "embedder": "st", "classifier_chain": "rule"}
        text = render_config_toml(answers, tmp_path / "corpus.db")
        parsed = self._parse(text)
        assert parsed["backend"]["kind"] == "sqlite"
        assert parsed["backend"]["dsn"].endswith("corpus.db")
        # No backslashes — even on Windows the path was rendered via
        # ``as_posix()`` so basic-string TOML doesn't misread it.
        assert "\\" not in parsed["backend"]["dsn"]

    def test_postgres_branch(self, tmp_path: Path) -> None:
        answers = {
            "backend": "postgres",
            "postgres_dsn": "postgresql://x:y@host:5432/cf",
            "embedder": "st",
            "classifier_chain": "rule",
        }
        text = render_config_toml(answers, tmp_path / "ignored.db")
        parsed = self._parse(text)
        assert parsed["backend"]["kind"] == "postgres"
        assert parsed["backend"]["dsn"] == "postgresql://x:y@host:5432/cf"

    def test_embedder_st_only(self, tmp_path: Path) -> None:
        answers = {"backend": "sqlite", "embedder": "st", "classifier_chain": "rule"}
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        providers = [e["provider"] for e in parsed["embedders"]]
        assert providers == ["sentence_transformers"]

    def test_embedder_openai_with_base_url(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "openai",
            "openai_api_key_env": "MY_KEY",
            "openai_base_url": "http://localhost:8000/v1",
            "classifier_chain": "rule",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        emb = parsed["embedders"][0]
        assert emb["provider"] == "openai"
        assert emb["api_key_env"] == "MY_KEY"
        assert emb["base_url"] == "http://localhost:8000/v1"

    def test_embedder_both(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "both",
            "openai_api_key_env": "OPENAI_API_KEY",
            "openai_base_url": "",
            "classifier_chain": "rule",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        providers = sorted(e["provider"] for e in parsed["embedders"])
        assert providers == ["openai", "sentence_transformers"]

    def test_vlm_block_only_with_ocr_yes(self, tmp_path: Path) -> None:
        answers_no = {"backend": "sqlite", "embedder": "st", "classifier_chain": "rule"}
        parsed_no = self._parse(render_config_toml(answers_no, tmp_path / "x.db"))
        assert "vlm" not in parsed_no

        answers_yes = {
            **answers_no,
            "ocr_escalation": "yes",
            "vlm_backend": "ollama",
            "vlm_ollama_url": "http://localhost:11434",
        }
        parsed_yes = self._parse(render_config_toml(answers_yes, tmp_path / "x.db"))
        assert parsed_yes["vlm"]["backend"] == "ollama"
        assert parsed_yes["vlm"]["ollama_url"] == "http://localhost:11434"

    def test_vlm_mistral_branch(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "st",
            "classifier_chain": "rule",
            "ocr_escalation": "yes",
            "vlm_backend": "mistral",
            "vlm_mistral_api_key_env": "MY_MISTRAL",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        assert parsed["vlm"]["backend"] == "mistral"
        assert parsed["vlm"]["mistral_api_key_env"] == "MY_MISTRAL"

    def test_whisper_local(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "st",
            "classifier_chain": "rule",
            "whisper_transcription": "yes",
            "whisper_backend": "local",
            "whisper_local_model": "medium",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        assert parsed["whisper"]["backend"] == "local"
        assert parsed["whisper"]["model"] == "medium"

    def test_whisper_remote(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "st",
            "classifier_chain": "rule",
            "whisper_transcription": "yes",
            "whisper_backend": "remote",
            "whisper_remote_base_url": "https://api.groq.com/openai/v1",
            "whisper_remote_api_key_env": "GROQ_API_KEY",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        assert parsed["whisper"]["remote_base_url"] == "https://api.groq.com/openai/v1"
        assert parsed["whisper"]["remote_api_key_env"] == "GROQ_API_KEY"

    def test_classifier_chain_rule_llm(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "st",
            "classifier_chain": "rule+llm",
            "classifier_llm_url": "http://hosted:11434",
            "classifier_llm_api_key_env": "CLASSIFIER_KEY",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        assert parsed["classifier"]["chain"] == ["rule", "llm"]
        assert parsed["classifier"]["llm_url"] == "http://hosted:11434"
        assert parsed["classifier"]["llm_api_key_env"] == "CLASSIFIER_KEY"

    def test_code_enricher_remote(self, tmp_path: Path) -> None:
        answers = {
            "backend": "sqlite",
            "embedder": "st",
            "classifier_chain": "rule",
            "code_ingest": "yes",
            "code_enricher": "remote",
            "code_enricher_remote_url": "https://qwen.local",
            "code_enricher_remote_api_shape": "openai",
            "code_enricher_remote_api_key_env": "QWEN_KEY",
        }
        parsed = self._parse(render_config_toml(answers, tmp_path / "x.db"))
        assert parsed["code_enricher"]["backend"] == "remote"
        assert parsed["code_enricher"]["remote_url"] == "https://qwen.local"
        assert parsed["code_enricher"]["remote_api_shape"] == "openai"

    def test_no_federation_block_by_default(self, tmp_path: Path) -> None:
        """RFC fleet-3 hard backcompat bar: the default render must NOT
        emit a ``[federation]`` block.

        Federation drift detection is opt-in (``[federation] enabled =
        true``); a freshly-wizarded config must look byte-for-byte like
        today's local-only setup, which means no ``[federation]`` section
        at all. The defaulted ``FederationConfig`` on ``Config`` still
        materialises (enabled=False) when the block is absent — pinned by
        ``test_federation_config.py`` — so omitting it from the render is
        the correct, behaviour-preserving choice."""
        answers = {"backend": "sqlite", "embedder": "st", "classifier_chain": "rule"}
        text = render_config_toml(answers, tmp_path / "x.db")
        assert "[federation]" not in text
        # And it still parses + carries no federation section.
        assert "federation" not in self._parse(text)

    def test_windows_style_path_does_not_corrupt_toml(self, tmp_path: Path) -> None:
        """Regression: a literal Windows-style path with backslashes
        must NOT trigger TOML basic-string ``\\U`` unicode-escape parse
        errors. The wizard uses ``as_posix()`` to side-step this — pin
        the invariant explicitly."""
        # The path mock pretends to be a Windows path.
        fake_db = Path("C:/Users/runner/AppData/Local/corpus.db")
        answers = {"backend": "sqlite", "embedder": "st", "classifier_chain": "rule"}
        text = render_config_toml(answers, fake_db)
        parsed = self._parse(text)
        # Round-trips cleanly through tomllib.
        assert "corpus.db" in parsed["backend"]["dsn"]


# ── secrets.env rendering ─────────────────────────────────────────────


class TestRenderSecretsEnv:
    def test_no_keys_when_no_remote_backends(self) -> None:
        text = render_secrets_env({"backend": "sqlite", "embedder": "st"})
        assert "no API keys required" in text

    def test_openai_key_listed(self) -> None:
        text = render_secrets_env({"embedder": "openai", "openai_api_key_env": "MY_KEY"})
        assert "MY_KEY=..." in text

    def test_no_duplicate_keys(self) -> None:
        """Same env var name across multiple prompts collapses to one line."""
        text = render_secrets_env(
            {
                "embedder": "openai",
                "openai_api_key_env": "OPENAI_API_KEY",
                "whisper_remote_api_key_env": "OPENAI_API_KEY",
            }
        )
        assert text.count("OPENAI_API_KEY=") == 1


# ── End-to-end: collect → write ───────────────────────────────────────


class TestRunNonInteractive:
    def test_writes_config_and_secrets(self, tmp_path: Path) -> None:
        env = {
            "CF_BACKEND": "sqlite",
            "CF_MULTI_FORMAT": "yes",
            "CF_CODE": "yes",
            "CF_OCR": "no",
            "CF_WHISPER": "no",
            "CF_TOKENS": "yes",
            "CF_RETRIEVAL": "yes",
            "CF_RERANKER": "no",
            "CF_EMBEDDER": "st",
            "CF_CLASSIFIER": "rule",
            "CF_MCP": "yes",
            "CF_HF": "yes",
            "CF_SUPERVISOR": "no",
        }
        config_path, secrets_path, answers = run_non_interactive(
            config_dir=tmp_path,
            env=env,
        )
        assert config_path.exists()
        assert secrets_path.exists()
        assert answers["backend"] == "sqlite"
        # Re-parse the written config to confirm it's valid TOML.
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert parsed["backend"]["kind"] == "sqlite"

    def test_falls_back_to_defaults_when_env_missing(self, tmp_path: Path) -> None:
        config_path, _secrets_path, answers = run_non_interactive(
            config_dir=tmp_path,
            env={},
        )
        # Default backend is sqlite per the question tree.
        assert answers["backend"] == "sqlite"
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert parsed["backend"]["kind"] == "sqlite"

    def test_preserves_existing_secrets_env(self, tmp_path: Path) -> None:
        # Pre-create a secrets.env with real content; the wizard
        # should NOT overwrite it.
        secrets_path = tmp_path / "secrets.env"
        secrets_path.write_text("REAL_KEY=already-set\n", encoding="utf-8")
        run_non_interactive(config_dir=tmp_path, env={"CF_BACKEND": "sqlite"})
        assert "REAL_KEY=already-set" in secrets_path.read_text(encoding="utf-8")


class TestRunWizard:
    def test_interactive_round_trip(self, tmp_path: Path) -> None:
        # Just hammer "Enter" through every prompt — the wizard takes
        # the defaults. Loads questions.toml so input length must match
        # the number of relevant prompts; "\n" lines is more than
        # enough and unused lines just stay on the stream.
        stream_in = io.StringIO("\n" * 100)
        stream_out = io.StringIO()
        config_path, _secrets_path, answers = run_wizard(
            config_dir=tmp_path,
            stream_in=stream_in,
            stream_out=stream_out,
        )
        assert config_path.exists()
        assert answers["backend"] == "sqlite"

    def test_prints_next_steps_calibration_hint(self, tmp_path: Path) -> None:
        """rfc-fleet-1: the interactive path ends with the bench hint."""
        stream_in = io.StringIO("\n" * 100)
        stream_out = io.StringIO()
        run_wizard(config_dir=tmp_path, stream_in=stream_in, stream_out=stream_out)
        out = stream_out.getvalue()
        assert "Next steps:" in out
        assert "corpus-forge bench embed --all" in out


# ── post-setup "next steps" calibration hint (rfc-fleet-1) ─────────────


class TestNextStepsHint:
    """Both setup paths surface the ``bench embed --all`` calibration hint."""

    def test_render_next_steps_contains_bench_calibration(self) -> None:
        from corpus_forge.setup import render_next_steps

        text = render_next_steps()
        assert "Next steps:" in text
        assert "corpus-forge bench embed --all" in text
        # The hint points the operator at the read verb.
        assert "models list" in text

    def test_non_interactive_path_writes_hint_to_stderr(self, tmp_path: Path, capsys) -> None:
        run_non_interactive(config_dir=tmp_path, env={"CF_BACKEND": "sqlite"})
        captured = capsys.readouterr()
        # Non-interactive keeps stdout clean; the hint lands on stderr.
        assert "corpus-forge bench embed --all" in captured.err


# ── _collect_answers depends_on threading ─────────────────────────────


class TestCollectAnswers:
    def test_irrelevant_prompts_skipped(self) -> None:
        """When ``code_ingest=no``, ``code_enricher`` (which depends on
        code_ingest=yes) is skipped entirely — its env-var fallback
        never lands in the answer map."""
        questions = load_questions()
        answers = _collect_answers(
            questions,
            interactive=False,
            env={"CF_CODE": "no"},
        )
        # code_ingest skipped → answers["code_enricher"] absent.
        assert answers["code_ingest"] == "no"
        assert "code_enricher" not in answers


# ── Phase M Wave 1 — create_corpusignore wiring ─────────────────────


class TestCreateCorpusignoreQuestion:
    """The wizard exposes a ``create_corpusignore`` yes/no question
    (env var ``CF_CREATE_CORPUSIGNORE``, default ``yes``).
    """

    def test_question_present(self) -> None:
        questions = load_questions()
        ids = {q.id for q in questions}
        assert "create_corpusignore" in ids
        q = next(q for q in questions if q.id == "create_corpusignore")
        assert q.type == "yes_no"
        assert q.default == "yes"
        assert q.env == "CF_CREATE_CORPUSIGNORE"


class TestApplyCorpusignoreNonInteractive:
    """End-to-end: ``run_non_interactive`` writes the managed block."""

    def _base_env(self, *, scan_root: str, whisper: str = "no") -> dict[str, str]:
        return {
            "CF_BACKEND": "sqlite",
            "CF_MULTI_FORMAT": "no",
            "CF_CODE": "no",
            "CF_OCR": "no",
            "CF_WHISPER": whisper,
            "CF_TOKENS": "no",
            "CF_RETRIEVAL": "no",
            "CF_RERANKER": "no",
            "CF_EMBEDDER": "st",
            "CF_CLASSIFIER": "rule",
            "CF_MCP": "no",
            "CF_HF": "no",
            "CF_SUPERVISOR": "no",
            "CF_CREATE_CORPUSIGNORE": "yes",
            "CF_SCAN_ROOT": scan_root,
        }

    def test_writes_corpusignore_at_scan_root(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        config_dir = tmp_path / "cf"
        env = self._base_env(scan_root=str(scan_root))
        run_non_interactive(config_dir=config_dir, env=env)
        ignore_path = scan_root / ".corpusignore"
        assert ignore_path.exists()
        text = ignore_path.read_text(encoding="utf-8")
        # Sentinels present.
        from corpus_forge.ignore_defaults import MANAGED_END, MANAGED_START

        assert MANAGED_START in text
        assert MANAGED_END in text

    def test_whisper_off_adds_audio_patterns(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        config_dir = tmp_path / "cf"
        env = self._base_env(scan_root=str(scan_root), whisper="no")
        run_non_interactive(config_dir=config_dir, env=env)
        text = (scan_root / ".corpusignore").read_text(encoding="utf-8")
        assert "*.mp4" in text
        assert "*.mp3" in text

    def test_whisper_on_drops_audio_patterns(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        config_dir = tmp_path / "cf"
        env = self._base_env(scan_root=str(scan_root), whisper="yes")
        # Provide whisper backend dependency so the deps-on tree wires.
        env["CF_WHISPER_BACKEND"] = "local"
        env["CF_WHISPER_LOCAL_MODEL"] = "small"
        run_non_interactive(config_dir=config_dir, env=env)
        text = (scan_root / ".corpusignore").read_text(encoding="utf-8")
        assert "*.mp4" not in text

    def test_blank_scan_root_writes_only_global(self, tmp_path: Path, monkeypatch) -> None:
        # No scan_root → no per-tree write, but the global at
        # <config_dir>/ignore gets resynced regardless.
        config_dir = tmp_path / "cf"
        env = self._base_env(scan_root="")
        # Pin global to inside the config dir for the duration of this
        # test; the wizard writes the global at <config_dir>/ignore.
        monkeypatch.setenv("CF_GLOBAL_IGNORE_FILE", str(config_dir / "ignore"))
        run_non_interactive(config_dir=config_dir, env=env)
        # No tree to put a .corpusignore under — verify by absence in tmp_path.
        leftover = list(tmp_path.rglob(".corpusignore"))
        assert leftover == []
        # Global written.
        assert (config_dir / "ignore").exists()

    def test_create_corpusignore_no_skips_local_write(self, tmp_path: Path) -> None:
        scan_root = tmp_path / "vault"
        scan_root.mkdir()
        config_dir = tmp_path / "cf"
        env = self._base_env(scan_root=str(scan_root))
        env["CF_CREATE_CORPUSIGNORE"] = "no"
        run_non_interactive(config_dir=config_dir, env=env)
        # User opted out — no per-tree .corpusignore.
        assert not (scan_root / ".corpusignore").exists()


class TestApplyCorpusignoreQuick:
    """``run_quick`` also honors the env var."""

    def test_quick_writes_corpusignore(self, tmp_path: Path) -> None:
        from corpus_forge.setup import run_quick

        scan_root = tmp_path / "tree"
        scan_root.mkdir()
        config_dir = tmp_path / "cf"
        env = {
            "CF_BACKEND": "sqlite",
            "CF_OLLAMA_URL": "http://localhost:11434",
            "CF_EMBEDDER_MODEL_ID": "qwen3:8b",
            "CF_DATASET_NAME": "default",
            "CF_SCAN_ROOT": str(scan_root),
            "CF_CREATE_CORPUSIGNORE": "yes",
        }
        run_quick(config_dir=config_dir, env=env, interactive=False)
        assert (scan_root / ".corpusignore").exists()
