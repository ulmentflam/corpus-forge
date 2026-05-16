"""Question-tree wizard for ``corpus-forge setup``.

This module reads ``packaging/install/questions.toml``, walks the user
(or env vars) through the prompts, and renders both ``config.toml``
and ``secrets.env`` under ``~/.config/corpus-forge/``.

The rendering is intentionally minimal — the question tree drives only
the canonical blocks (``[backend]``, ``[vlm]``, ``[whisper]``,
``[classifier]``, ``[code_enricher]``, etc.) so existing manual
edits to ``config.toml`` are preserved on re-run.

Test scope:

- :func:`load_questions` is parser-only and tested against the bundled
  ``questions.toml`` in ``tests/unit/test_install_questions_schema.py``.
- :func:`render_config_toml` is tested via :mod:`tests.unit.test_setup_wizard`
  for every supported backend selector branch.
- :func:`run_non_interactive` is exercised end-to-end by the install-
  smoke workflow under ``CF_NON_INTERACTIVE=1`` in
  ``.github/workflows/install-smoke.yml``.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

# Canonical question tree ships INSIDE the corpus_forge package (not in
# repo-root ``packaging/``) so it's bundled in the wheel and the wizard
# can find it post-``uv tool install``. The shell installers also read
# this same file from the source tree (via ``corpus_forge/setup/`` path
# or the raw.githubusercontent.com URL) so there's no duplication.
DEFAULT_QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.toml"

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "corpus-forge"


@dataclass(frozen=True)
class Question:
    """One [[question]] block from ``questions.toml``."""

    id: str
    prompt: str
    type: str  # "yes_no" | "choice" | "text"
    default: str = ""
    env: str = ""
    depends_on: str = ""
    warn: str = ""
    choices: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    config_block: str = ""

    def is_relevant(self, answers: dict[str, str]) -> bool:
        """``depends_on = "<id>=<value>"`` predicate."""
        if not self.depends_on:
            return True
        target, _, value = self.depends_on.partition("=")
        return answers.get(target) == value


def load_questions(path: Path | None = None) -> list[Question]:
    """Parse ``questions.toml`` and return the ordered list of Questions.

    Args:
        path: Optional override. Defaults to the bundled
            ``packaging/install/questions.toml``.
    """
    if path is None:
        path = DEFAULT_QUESTIONS_PATH
    with path.open("rb") as f:
        data = tomllib.load(f)
    return [
        Question(
            id=q["id"],
            prompt=q["prompt"],
            type=q["type"],
            default=q.get("default", ""),
            env=q["env"],
            depends_on=q.get("depends_on", ""),
            warn=q.get("warn", ""),
            choices=list(q.get("choices", [])),
            extras=list(q.get("extras", [])),
            config_block=q.get("config_block", ""),
        )
        for q in data["question"]
    ]


# ───────────────────────────────────────────────────────────────────────────
# Answer collection
# ───────────────────────────────────────────────────────────────────────────


def _normalise_yes_no(raw: str, default: str) -> str | None:
    """Return ``"yes"`` / ``"no"`` or ``None`` for unparseable input."""
    if not raw:
        return default
    lowered = raw.strip().lower()
    if lowered in {"y", "yes"}:
        return "yes"
    if lowered in {"n", "no"}:
        return "no"
    return None


def _read_answer_interactive(q: Question, *, stream_in: IO[str], stream_out: IO[str]) -> str:
    """Prompt the user via ``stream_in`` (defaults to stdin in real use).

    Stream-injected so tests can drive the prompt without a TTY.
    """
    if q.warn:
        # ASCII glyph: Windows consoles default to cp1252/cp437 which
        # can't encode ⚠. The shell installers use the fancy glyph;
        # the Python wizard stays cross-platform safe.
        stream_out.write(f"[WARN] {q.warn}\n")
        stream_out.flush()

    hint = ""
    if q.type == "yes_no":
        hint = " [Y/n]" if q.default == "yes" else " [y/N]"
    elif q.type == "choice":
        hint = f" [{'/'.join(q.choices)}]"

    while True:
        stream_out.write(f"{q.prompt}{hint} (default: {q.default}) ")
        stream_out.flush()
        raw = stream_in.readline().rstrip("\n").rstrip("\r")
        answer = raw or q.default

        if q.type == "yes_no":
            normalised = _normalise_yes_no(answer, q.default)
            if normalised is None:
                stream_out.write("Please answer y or n.\n")
                continue
            return normalised
        if q.type == "choice" and answer not in q.choices:
            stream_out.write(f"Please pick one of: {', '.join(q.choices)}\n")
            continue
        return answer


def _read_answer_non_interactive(q: Question, env: dict[str, str]) -> str:
    """Read from ``env[q.env]`` or fall back to ``q.default``.

    Returns the answer as a string. Non-interactive mode never fails
    loud — missing values silently fall back to the question's
    default. CI matrices set ``CF_*`` for every prompt they care about.
    """
    raw = env.get(q.env, "")
    if q.type == "yes_no":
        normalised = _normalise_yes_no(raw, q.default)
        return normalised if normalised is not None else q.default
    if not raw:
        return q.default
    if q.type == "choice" and raw not in q.choices:
        return q.default
    return raw


# ───────────────────────────────────────────────────────────────────────────
# Config rendering
# ───────────────────────────────────────────────────────────────────────────


def _quote_toml_str(s: str) -> str:
    """Render ``s`` as a TOML basic string with backslashes / quotes escaped.

    Using ``as_posix()`` upstream is preferred (avoids the ``\\U`` /
    ``\\u`` unicode-escape gotcha on Windows paths), but for any value
    that genuinely contains a backslash this guards the rendered
    config.
    """
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config_toml(answers: dict[str, str], db_path: Path) -> str:
    """Render ``config.toml`` text from the wizard's answers.

    Pure function (no side effects, no filesystem reads) so it can be
    unit-tested directly. ``db_path`` is the SQLite DB location when
    ``backend=sqlite``; ignored otherwise.

    The rendered config is intentionally minimal — only the blocks the
    wizard drives are emitted. Users keep their hand-rolled
    ``config.example.toml`` tweaks by editing the file post-install.
    """
    out: list[str] = []
    out.append("# Generated by `corpus-forge setup`. Edit freely after the wizard.")
    out.append("")

    # ── [backend] ──────────────────────────────────────────────────────
    out.append("[backend]")
    backend = answers.get("backend", "sqlite")
    if backend == "sqlite":
        out.append('kind = "sqlite"')
        # ``as_posix()`` is critical: a Windows path baked into a TOML
        # basic string would be interpreted as a unicode escape and
        # corrupt the config.
        out.append(f"dsn  = {_quote_toml_str(db_path.as_posix())}")
    else:
        out.append('kind = "postgres"')
        dsn = answers.get("postgres_dsn", "postgresql://localhost:5432/corpus_forge")
        out.append(f"dsn  = {_quote_toml_str(dsn)}")
    out.append("")
    out.append("[daemon]")
    out.append("")

    # ── [[datasets]] ───────────────────────────────────────────────────
    # Minimal default dataset; users can add more later.
    out.append("[[datasets]]")
    out.append('name = "default"')
    out.append('kind = "text"')
    out.append(
        'sources = [{plugin = "markdown_vault", '
        'vault_root = "~/Documents/notes", chunker = "markdown"}]'
    )
    out.append("")

    # ── [[embedders]] ──────────────────────────────────────────────────
    embedder = answers.get("embedder", "st")
    if embedder in {"st", "both"}:
        out.append("[[embedders]]")
        out.append('name      = "qwen3_8b"')
        out.append('provider  = "sentence_transformers"')
        out.append('model_id  = "Qwen/Qwen3-Embedding-8B"')
        out.append("dimension = 4096")
        out.append("normalize = true")
        out.append('distance  = "cosine"')
        out.append("active    = true")
        out.append("")
    if embedder in {"openai", "both"}:
        out.append("[[embedders]]")
        out.append('name      = "openai_3l"')
        out.append('provider  = "openai"')
        out.append('model_id  = "text-embedding-3-large"')
        out.append("dimension = 3072")
        out.append("normalize = true")
        out.append('distance  = "cosine"')
        out.append("active    = true")
        key_env = answers.get("openai_api_key_env", "OPENAI_API_KEY") or "OPENAI_API_KEY"
        out.append(f"api_key_env = {_quote_toml_str(key_env)}")
        base_url = answers.get("openai_base_url", "") or ""
        if base_url:
            out.append(f"base_url = {_quote_toml_str(base_url)}")
        out.append("")

    # ── [vlm] (only when OCR escalation is enabled) ────────────────────
    if answers.get("ocr_escalation") == "yes":
        vlm = answers.get("vlm_backend", "ollama")
        out.append("[vlm]")
        out.append(f"backend = {_quote_toml_str(vlm)}")
        if vlm == "ollama":
            ollama_url = answers.get("vlm_ollama_url", "http://localhost:11434")
            out.append(f"ollama_url = {_quote_toml_str(ollama_url)}")
        else:
            key_env = answers.get("vlm_mistral_api_key_env", "MISTRAL_API_KEY")
            out.append(f"mistral_api_key_env = {_quote_toml_str(key_env)}")
        out.append("")

    # ── [whisper] (only when audio/video is enabled) ───────────────────
    if answers.get("whisper_transcription") == "yes":
        wb = answers.get("whisper_backend", "local")
        out.append("[whisper]")
        out.append(f"backend = {_quote_toml_str(wb)}")
        if wb == "local":
            model = answers.get("whisper_local_model", "small")
            out.append(f"model = {_quote_toml_str(model)}")
        else:
            base_url = answers.get("whisper_remote_base_url", "https://api.openai.com/v1")
            key_env = answers.get("whisper_remote_api_key_env", "OPENAI_API_KEY")
            out.append(f"remote_base_url = {_quote_toml_str(base_url)}")
            out.append(f"remote_api_key_env = {_quote_toml_str(key_env)}")
        out.append("")

    # ── [classifier] ───────────────────────────────────────────────────
    chain = answers.get("classifier_chain", "rule")
    chain_list = '["rule"]' if chain == "rule" else '["rule", "llm"]'
    out.append("[classifier]")
    out.append(f"chain = {chain_list}")
    if chain == "rule+llm":
        llm_url = answers.get("classifier_llm_url", "http://localhost:11434")
        out.append(f"llm_url = {_quote_toml_str(llm_url)}")
        key_env = answers.get("classifier_llm_api_key_env", "")
        if key_env:
            out.append(f"llm_api_key_env = {_quote_toml_str(key_env)}")
    out.append("")

    # ── [code_enricher] (only when code ingest is on) ──────────────────
    if answers.get("code_ingest") == "yes":
        enricher = answers.get("code_enricher", "none")
        out.append("[code_enricher]")
        out.append(f"backend = {_quote_toml_str(enricher)}")
        if enricher == "local":
            url = answers.get("code_enricher_url", "http://localhost:11434")
            out.append(f"local_url = {_quote_toml_str(url)}")
        elif enricher == "remote":
            url = answers.get("code_enricher_remote_url", "http://localhost:11434")
            shape = answers.get("code_enricher_remote_api_shape", "ollama")
            key_env = answers.get("code_enricher_remote_api_key_env", "OLLAMA_API_KEY")
            out.append(f"remote_url = {_quote_toml_str(url)}")
            out.append(f"remote_api_shape = {_quote_toml_str(shape)}")
            out.append(f"remote_api_key_env = {_quote_toml_str(key_env)}")
        out.append("")

    return "\n".join(out) + "\n"


def render_secrets_env(answers: dict[str, str]) -> str:
    """Render a stub ``secrets.env`` listing every env var the user
    needs to fill in.

    We don't ask for the actual secrets at install time — those go in
    ``secrets.env`` by convention. The wizard just emits a template
    pointing at the right variable names.
    """
    out: list[str] = [
        "# Generated by `corpus-forge setup`. Fill in real values; do NOT commit.",
        "",
    ]
    seen: set[str] = set()

    candidates: list[tuple[str, str]] = []
    # (env_var_id_in_answers, label)
    if answers.get("openai_api_key_env"):
        candidates.append((answers["openai_api_key_env"], "OpenAI embedder"))
    if answers.get("vlm_mistral_api_key_env"):
        candidates.append((answers["vlm_mistral_api_key_env"], "Mistral OCR API"))
    if answers.get("whisper_remote_api_key_env"):
        candidates.append((answers["whisper_remote_api_key_env"], "Remote Whisper"))
    if answers.get("classifier_llm_api_key_env"):
        candidates.append((answers["classifier_llm_api_key_env"], "LLM classifier"))
    if answers.get("code_enricher_remote_api_key_env") and answers.get("code_enricher") == "remote":
        candidates.append((answers["code_enricher_remote_api_key_env"], "Remote code enricher"))

    for env_name, label in candidates:
        if env_name in seen:
            continue
        seen.add(env_name)
        out.append(f"# {label}")
        out.append(f"# {env_name}=...")
        out.append("")

    if not seen:
        out.append("# (no API keys required for your current configuration)")
        out.append("")

    return "\n".join(out) + "\n"


# ───────────────────────────────────────────────────────────────────────────
# Entry points
# ───────────────────────────────────────────────────────────────────────────


def _write_config(
    config_dir: Path,
    answers: dict[str, str],
) -> tuple[Path, Path]:
    """Write ``config.toml`` + ``secrets.env`` into ``config_dir``.

    Returns the two output paths. Idempotent — overwrites the existing
    config when run twice; the user is expected to back up their own
    edits before re-running the wizard.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    db_path = (config_dir / "corpus.db").resolve()
    config_path = config_dir / "config.toml"
    secrets_path = config_dir / "secrets.env"

    config_path.write_text(render_config_toml(answers, db_path), encoding="utf-8")
    if not secrets_path.exists():
        secrets_path.write_text(render_secrets_env(answers), encoding="utf-8")

    return config_path, secrets_path


def _collect_answers(
    questions: list[Question],
    *,
    interactive: bool,
    env: dict[str, str],
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> dict[str, str]:
    """Walk the question tree once and return the {id: answer} map."""
    answers: dict[str, str] = {}
    in_stream = stream_in or sys.stdin
    out_stream = stream_out or sys.stdout

    for q in questions:
        if not q.is_relevant(answers):
            continue
        if interactive:
            answers[q.id] = _read_answer_interactive(q, stream_in=in_stream, stream_out=out_stream)
        else:
            answers[q.id] = _read_answer_non_interactive(q, env)
    return answers


def run_wizard(
    *,
    config_dir: Path | None = None,
    questions_path: Path | None = None,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    """Interactive wizard. Walks the user through prompts and writes config."""
    questions = load_questions(questions_path)
    answers = _collect_answers(
        questions,
        interactive=True,
        env=dict(os.environ),
        stream_in=stream_in,
        stream_out=stream_out,
    )
    config_path, secrets_path = _write_config(config_dir or DEFAULT_CONFIG_DIR, answers)
    return config_path, secrets_path, answers


def run_non_interactive(
    *,
    config_dir: Path | None = None,
    questions_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    """CI / unattended wizard. Reads answers from ``CF_*`` env vars."""
    questions = load_questions(questions_path)
    answers = _collect_answers(
        questions,
        interactive=False,
        env=env or dict(os.environ),
    )
    config_path, secrets_path = _write_config(config_dir or DEFAULT_CONFIG_DIR, answers)
    return config_path, secrets_path, answers
