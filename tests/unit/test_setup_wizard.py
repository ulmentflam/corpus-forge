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
