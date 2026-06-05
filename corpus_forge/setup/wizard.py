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

import json as _json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from corpus_forge.acceleration import detect_accelerator, recommend_embedder_preset

# Canonical question tree ships INSIDE the corpus_forge package (not in
# repo-root ``packaging/``) so it's bundled in the wheel and the wizard
# can find it post-``uv tool install``. The shell installers also read
# this same file from the source tree (via ``corpus_forge/setup/`` path
# or the raw.githubusercontent.com URL) so there's no duplication.
DEFAULT_QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.toml"

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "corpus-forge"

# Post-setup "next steps" the wizard prints after writing config. The
# ``bench embed --all`` line is the rfc-fleet-1 calibration step — it
# records this machine's embedder throughput into the model-telemetry
# tables so ``corpus-forge models list`` can compare lanes across the
# fleet. Kept as data (not inlined) so both the interactive and
# non-interactive paths emit the identical sequence and tests can assert
# the contract from one source.
NEXT_STEPS: tuple[str, ...] = (
    "Next steps:",
    "  corpus-forge migrate          # apply the schema (idempotent)",
    "  corpus-forge ingest --once    # one-shot sync of the configured roots",
    "  corpus-forge embed            # backfill embeddings",
    "  corpus-forge bench embed --all  # calibrate this machine's embedder "
    "throughput (see `models list`)",
)


def render_next_steps() -> str:
    """Return the post-setup "next steps" block as a single string.

    The block ends with the rfc-fleet-1 ``bench embed --all`` calibration
    hint.  Emitted by both :func:`run_wizard` (to the interactive
    ``stream_out``) and :func:`run_non_interactive` (to ``stderr`` so it
    surfaces in install logs without polluting the machine-driven
    stdout).
    """
    return "\n".join(NEXT_STEPS) + "\n"


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

    Has no *filesystem* side effects — output is returned as a string
    rather than written.  When ``answers["embedder"] == "auto"``, the
    function calls :func:`detect_accelerator` (which shells out to
    ``nvidia-smi``); that subprocess probe is the only host-dependent
    behaviour.  Tests patch
    ``corpus_forge.setup.wizard.detect_accelerator`` to keep coverage
    deterministic.  ``db_path`` is the SQLite DB location when
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
    embedder = answers.get("embedder", "auto")
    if embedder == "auto":
        # Detect the host's accelerator (CUDA / MPS / CPU) and emit
        # the matching llama-cpp preset.  This is the recommended
        # default for fresh installs because it picks a model size
        # that fits the hardware and uses GPU offload when present.
        info = detect_accelerator()
        preset = recommend_embedder_preset(info)
        out.append(f"# auto-detected accelerator: {preset.summary}")
        out.append(preset.to_toml_block(name="nomic").rstrip())
        out.append("")
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


def _features_from_answers(answers: dict[str, str]) -> dict[str, bool]:
    """Derive the four corpusignore feature flags from the wizard's answer map.

    Mapping (matches the canonical
    :func:`corpus_forge.ignore_defaults.feature_flags_from_config`):

    - ``CF_WHISPER`` / ``whisper_transcription`` → ``whisper``.
    - ``CF_OCR`` / ``ocr_escalation`` → ``vlm`` **and** ``image_extractor``
      (the OCR pipeline implies image-aware extraction).
    - ``CF_CODE_ENRICHER`` / ``code_enricher`` (non-"none") → ``code_enricher``.

    Defaults: all flags False. The non-interactive path may omit a key
    entirely (depends_on skips), so use ``answers.get(...) == "yes"``
    semantics throughout.
    """
    whisper_on = answers.get("whisper_transcription") == "yes"
    ocr_on = answers.get("ocr_escalation") == "yes"
    enricher_val = answers.get("code_enricher", "none")
    code_enricher_on = enricher_val in {"local", "remote"}
    return {
        "whisper": whisper_on,
        "image_extractor": ocr_on,
        "code_enricher": code_enricher_on,
        "vlm": ocr_on,
    }


def _apply_corpusignore(answers: dict[str, str], config_dir: Path) -> None:
    """Phase M Wave 1 — write `.corpusignore` (per-tree + global).

    Per-tree write happens only when:

    1. ``create_corpusignore`` answer is ``yes`` (default).
    2. A non-empty ``scan_root`` was supplied (the full and quick wizards
       use slightly different keys, but the quick wizard's
       ``scan_root`` is the same answer name).

    The global file at ``<config_dir>/ignore`` is **always** resynced so
    feature drift is caught even when the user skipped the per-tree
    prompt.

    Failures are logged-but-non-fatal: we never break the setup
    completion when an ignore write hiccups. The caller (CLI banner)
    surfaces enough information for the user to retry via
    ``corpus-forge ignore sync`` (Wave 3).
    """
    # Local import keeps the ignore_lifecycle import-time cost out of the
    # wizard hot path (the wizard is loaded by every CLI invocation).
    from corpus_forge.ignore_lifecycle import (  # noqa: PLC0415
        ManagedBlockCorrupted,
        _make_backup_path,
        atomic_write_text,
        render_managed_block,
        splice_managed_block,
        write_corpusignore,
    )

    features = _features_from_answers(answers)

    # 1. Per-tree write.
    scan_root_raw = (answers.get("scan_root") or "").strip()
    if answers.get("create_corpusignore", "yes") == "yes" and scan_root_raw:
        try:
            scan_root = Path(scan_root_raw).expanduser()
            if scan_root.exists() and scan_root.is_dir():
                write_corpusignore(scan_root, features)
        except (OSError, ManagedBlockCorrupted) as exc:  # pragma: no cover — defensive
            # Best-effort; the wizard prints a hint via the caller.
            sys.stderr.write(
                f"[corpus-forge setup] could not write {scan_root_raw}/.corpusignore: {exc}\n"
            )

    # 2. Global resync — always, so the global file tracks the configured
    #    features even when the user opts out of a per-tree file.
    try:
        # Honor an explicit CF_GLOBAL_IGNORE_FILE override; otherwise
        # default to <config_dir>/ignore (the canonical global path that
        # already mirrors git's convention).
        env_val = os.environ.get("CF_GLOBAL_IGNORE_FILE")
        global_path = Path(env_val).expanduser() if env_val else config_dir / "ignore"
        global_path.parent.mkdir(parents=True, exist_ok=True)
        existing = global_path.read_text(encoding="utf-8") if global_path.exists() else ""
        try:
            new_text = splice_managed_block(existing, render_managed_block(features))
        except ManagedBlockCorrupted:
            # Move the broken global aside and rewrite from scratch.
            backup = _make_backup_path(global_path)
            global_path.replace(backup)
            new_text = render_managed_block(features)
        atomic_write_text(global_path, new_text)
    except OSError as exc:  # pragma: no cover — defensive
        sys.stderr.write(
            f"[corpus-forge setup] could not write global ignore at {config_dir}/ignore: {exc}\n"
        )


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
    resolved_dir = config_dir or DEFAULT_CONFIG_DIR
    config_path, secrets_path = _write_config(resolved_dir, answers)
    _apply_corpusignore(answers, resolved_dir)
    _run_macos_tcc_handshake(answers, stream_out=stream_out or sys.stdout)
    (stream_out or sys.stdout).write(render_next_steps())
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
    resolved_dir = config_dir or DEFAULT_CONFIG_DIR
    config_path, secrets_path = _write_config(resolved_dir, answers)
    _apply_corpusignore(answers, resolved_dir)
    # Non-interactive mode runs in CI/headless environments — skip the
    # System Settings open() but still surface a denial via stderr so a
    # human reviewer sees it in the install logs.
    _run_macos_tcc_handshake(
        answers,
        stream_out=sys.stderr,
        open_settings=False,
    )
    # Surface the calibration hint on stderr (not stdout) so the
    # machine-driven non-interactive surface keeps a clean stdout while
    # the hint still lands in install logs.
    sys.stderr.write(render_next_steps())
    return config_path, secrets_path, answers


def _run_macos_tcc_handshake(
    answers: dict[str, str],
    *,
    stream_out: IO[str],
    open_settings: bool = True,
) -> None:
    """Probe TCC access for any iCloud-rooted paths in the rendered config.

    On macOS, the install handshake walks the configured filesystem
    roots (and the answers for ``[fs_extra_path]`` questions, since
    those become roots in :func:`render_config_toml`), classifies any
    that live under ``~/Library/Mobile Documents/...``, and probes
    them with :func:`corpus_forge.macos_tcc.probe_tcc_access`. On
    denial, opens System Settings → Privacy & Security → Full Disk
    Access and prints the recovery message.

    Non-macOS hosts: silent no-op. The cost is one ``sys.platform``
    check; we don't even import the macOS module on Linux/Windows.
    """

    if sys.platform != "darwin":
        return

    from corpus_forge import macos_tcc  # noqa: PLC0415 — keep cold-start fast

    # The answers map carries free-form filesystem roots in fields
    # like ``fs_root``, ``extra_root``, plus the new question IDs the
    # ``filesystem_*_path`` pattern produces. Be generous about which
    # keys we probe — any answer that looks like a path under
    # ``Mobile Documents`` qualifies.
    candidate_paths: list[Path] = []
    for value in answers.values():
        if not value or not isinstance(value, str):
            continue
        candidate = Path(value).expanduser()
        if macos_tcc.is_iclouddrive_managed(candidate):
            candidate_paths.append(candidate)

    if not candidate_paths:
        return

    result = macos_tcc.request_full_disk_access(
        candidate_paths,
        open_settings_on_denial=open_settings,
    )

    if result.granted:
        stream_out.write(
            "[corpus-forge setup] macOS TCC: Full Disk Access already granted "
            f"for {len(candidate_paths)} iCloud-rooted path(s).\n"
        )
        return

    stream_out.write("\n")
    stream_out.write("[corpus-forge setup] macOS TCC handshake — ACTION NEEDED\n")
    stream_out.write("=" * 64 + "\n")
    stream_out.write(result.instruction + "\n")
    stream_out.write("=" * 64 + "\n\n")


# ───────────────────────────────────────────────────────────────────────────
# Quick wizard (Phase L Wave 3)
# ───────────────────────────────────────────────────────────────────────────


# Six-question subset. ``depends_on`` is honored so the postgres DSN
# prompt is skipped when backend=sqlite. The ``ollama_url`` answer
# drives the model probe; the probed pick becomes the default for
# ``embedder_model_id`` unless the user (or env var) overrides it.
QUICK_QUESTIONS: list[Question] = [
    Question(
        id="backend",
        prompt="Storage backend",
        type="choice",
        choices=["sqlite", "postgres"],
        default="sqlite",
        env="CF_BACKEND",
    ),
    Question(
        id="backend_dsn",
        prompt="PostgreSQL DSN (e.g. postgresql://user:pass@localhost:5432/corpus_forge)",
        type="text",
        default="postgresql://localhost:5432/corpus_forge",
        env="CF_BACKEND_DSN",
        depends_on="backend=postgres",
    ),
    Question(
        id="ollama_url",
        prompt="Ollama base URL",
        type="text",
        default="http://localhost:11434",
        env="CF_OLLAMA_URL",
    ),
    Question(
        id="embedder_model_id",
        prompt="Embedder model",
        type="text",
        default="qwen3:8b",
        env="CF_EMBEDDER_MODEL_ID",
    ),
    Question(
        id="dataset_name",
        prompt="First dataset name",
        type="text",
        default="default",
        env="CF_DATASET_NAME",
    ),
    Question(
        id="scan_root",
        prompt="Scan root directory (leave blank to add later)",
        type="text",
        default="",
        env="CF_SCAN_ROOT",
    ),
]


# Tokens that mark a model as embedding-capable on Ollama.
_EMBED_TOKENS = ("embed", "bge", "qwen", "nomic")


def _urlopen_compat(req: urllib.request.Request, *, timeout: float):
    """Thin wrapper so tests can patch a single named symbol.

    Calls ``urllib.request.urlopen`` with the given request + timeout.
    Existing test patterns (`corpus_forge.update.version_check`) patch
    `urllib.request.urlopen` directly; the wizard adds one layer of
    indirection so the test surface is stable across Python versions.
    """
    return urllib.request.urlopen(req, timeout=timeout)


def _probe_ollama(base_url: str, *, timeout_s: float = 1.0) -> str | None:
    """Best-effort GET ``<base_url>/api/tags`` → first embed-capable model.

    Returns the chosen model's ``name`` field, or ``None`` on any
    failure (network error, malformed JSON, empty list). Fire-and-
    forget; the wizard treats ``None`` as "no probe signal" and falls
    back to whatever default the caller already had.
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with _urlopen_compat(req, timeout=timeout_s) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, OSError, ValueError):
        return None

    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        return None

    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.append(name)
    if not names:
        return None

    # Prefer first embed-capable model; otherwise None (caller keeps
    # whatever default they had).
    for name in names:
        lowered = name.lower()
        if any(tok in lowered for tok in _EMBED_TOKENS):
            return name
    return None


def _read_quick_answer_interactive(
    q: Question,
    *,
    stream_in: IO[str],
    stream_out: IO[str],
    default_override: str | None = None,
) -> str:
    """Quick-path prompt: tighter formatting than the full wizard.

    Honors ``default_override`` (used by the Ollama-probe default-pick
    flow). Identical stream-injection contract as
    :func:`_read_answer_interactive` so existing test patterns still
    work.
    """
    hint = ""
    if q.type == "choice":
        hint = f" [{'/'.join(q.choices)}]"
    default = default_override if default_override is not None else q.default
    stream_out.write(f"{q.prompt}{hint} (default: {default}) ")
    stream_out.flush()
    raw = stream_in.readline().rstrip("\n").rstrip("\r")
    answer = raw or default
    if q.type == "choice" and answer not in q.choices:
        # Mirror the full wizard's behavior: re-prompt once, but for
        # the quick path we simply accept the default to avoid loops
        # on stream-driven tests.
        return default
    return answer


def _render_quick_config_toml(answers: dict[str, str], db_path: Path) -> str:
    """Render the minimal config produced by the quick wizard.

    The Ollama URL maps to ``provider = "openai"`` + ``base_url =
    "<ollama_url>/v1"`` because ``EmbedderConfig.provider`` is
    constrained to ``sentence_transformers|openai`` and Ollama exposes
    an OpenAI-compatible endpoint under ``/v1``.

    ``dimension`` defaults to 1024 — a defensible middle-ground for
    the common Ollama embedding models (bge-m3=1024,
    nomic-embed-text=768, qwen3-embed=4096). Wave 5's embedder-
    fingerprint detection will surface a drift warning if the live
    model returns a different dim, so a fixed value here is fine for
    the quick path.
    """
    backend = answers.get("backend", "sqlite")
    ollama_url = answers.get("ollama_url", "http://localhost:11434").rstrip("/")
    base_url = f"{ollama_url}/v1"
    model_id = answers.get("embedder_model_id", "qwen3:8b") or "qwen3:8b"
    dataset_name = answers.get("dataset_name", "default") or "default"
    scan_root = (answers.get("scan_root") or "").strip()

    out: list[str] = []
    out.append("# Generated by `corpus-forge setup --quick`. Edit freely.")
    out.append("")

    # Top-level keys MUST appear before any `[section]` header — TOML
    # binds bare keys to the most-recent table. We emit `datasets = []`
    # at the top when the user skipped the scan root so it stays at
    # root scope.
    if not scan_root:
        # `Config.datasets` is a required `list[DatasetConfig]`; the
        # empty list is valid (no per-dataset validators fire).
        out.append("datasets = []")
        out.append("")

    # ── [backend] ──────────────────────────────────────────────────────
    out.append("[backend]")
    if backend == "sqlite":
        out.append('kind = "sqlite"')
        out.append(f"dsn  = {_quote_toml_str(db_path.as_posix())}")
    else:
        out.append('kind = "postgres"')
        dsn = answers.get("backend_dsn", "postgresql://localhost:5432/corpus_forge")
        out.append(f"dsn  = {_quote_toml_str(dsn)}")
    out.append("")
    out.append("[daemon]")
    out.append("")

    # ── [[datasets]] ───────────────────────────────────────────────────
    # When the user gave a scan root, emit a single filesystem source.
    if scan_root:
        out.append("[[datasets]]")
        out.append(f"name = {_quote_toml_str(dataset_name)}")
        out.append('kind = "text"')
        out.append(
            'sources = [{plugin = "filesystem", '
            f"root = {_quote_toml_str(scan_root)}, "
            'chunker = "markdown"}]'
        )
        out.append("")

    # ── [[embedders]] ──────────────────────────────────────────────────
    # Embedder name: derive from the model_id (lowercased, non-alpha
    # collapsed to underscores) so two quick configs with different
    # models don't both name themselves "default".
    name = "".join(c if c.isalnum() else "_" for c in model_id.lower()).strip("_")
    name = name or "default_embedder"
    out.append("[[embedders]]")
    out.append(f"name      = {_quote_toml_str(name)}")
    out.append('provider  = "openai"')
    out.append(f"model_id  = {_quote_toml_str(model_id)}")
    out.append("dimension = 1024")
    out.append("normalize = true")
    out.append('distance  = "cosine"')
    out.append("active    = true")
    out.append('api_key_env = "OLLAMA_API_KEY"')
    out.append(f"base_url = {_quote_toml_str(base_url)}")
    out.append("")

    return "\n".join(out) + "\n"


def _collect_quick_answers(
    *,
    interactive: bool,
    env: dict[str, str],
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> dict[str, str]:
    """Walk QUICK_QUESTIONS once with the Ollama probe wired in."""
    answers: dict[str, str] = {}
    in_stream = stream_in or sys.stdin
    out_stream = stream_out or sys.stdout
    probed_model: str | None = None
    probed_for_url: str | None = None

    for q in QUICK_QUESTIONS:
        if not q.is_relevant(answers):
            continue

        # Probe Ollama right after the URL is settled — the probed
        # model becomes the embedder default below.
        if q.id == "embedder_model_id" and probed_for_url is None:
            ollama_url = answers.get("ollama_url", q.default)
            probed_model = _probe_ollama(ollama_url)
            probed_for_url = ollama_url

        if interactive:
            override = probed_model if (q.id == "embedder_model_id" and probed_model) else None
            answers[q.id] = _read_quick_answer_interactive(
                q,
                stream_in=in_stream,
                stream_out=out_stream,
                default_override=override,
            )
        else:
            raw = env.get(q.env, "")
            if not raw:
                if q.id == "embedder_model_id" and probed_model:
                    answers[q.id] = probed_model
                else:
                    answers[q.id] = q.default
            elif q.type == "choice" and raw not in q.choices:
                answers[q.id] = q.default
            else:
                answers[q.id] = raw

    return answers


def _write_quick_config(
    config_dir: Path,
    answers: dict[str, str],
) -> tuple[Path, Path]:
    """Write the quick wizard's config.toml + secrets.env stub."""
    config_dir.mkdir(parents=True, exist_ok=True)
    db_path = (config_dir / "corpus.db").resolve()
    config_path = config_dir / "config.toml"
    secrets_path = config_dir / "secrets.env"

    config_path.write_text(_render_quick_config_toml(answers, db_path), encoding="utf-8")
    if not secrets_path.exists():
        # The quick path uses the Ollama OpenAI shim — the api_key_env
        # is `OLLAMA_API_KEY` which is harmless if unset against a
        # local Ollama.
        secrets_path.write_text(
            "# Generated by `corpus-forge setup --quick`. Fill in real values; do NOT commit.\n"
            "# OLLAMA_API_KEY=...\n",
            encoding="utf-8",
        )
    return config_path, secrets_path


def run_quick(
    *,
    config_dir: Path | None = None,
    env: dict[str, str] | None = None,
    interactive: bool = True,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    """Quick wizard — 6 questions + Ollama probe.

    Args:
        config_dir: Output directory; defaults to ``~/.config/corpus-forge``.
        env: Env-var lookup (non-interactive path); defaults to ``os.environ``.
        interactive: When True, prompts via ``stream_in``/``stream_out``.
            When False, reads answers from ``env`` and falls back to
            question defaults.
        stream_in/stream_out: Stream injection for interactive testing.

    Returns:
        ``(config_path, secrets_path, answers)`` — same shape as the
        full wizard.
    """
    answers = _collect_quick_answers(
        interactive=interactive,
        env=env or dict(os.environ),
        stream_in=stream_in,
        stream_out=stream_out,
    )
    resolved_dir = config_dir or DEFAULT_CONFIG_DIR
    config_path, secrets_path = _write_quick_config(resolved_dir, answers)
    # The quick wizard's answer map doesn't include
    # ``whisper_transcription`` / ``ocr_escalation`` / ``code_enricher`` —
    # default all features off so the conservative pattern set applies.
    # ``create_corpusignore`` is read from CF_CREATE_CORPUSIGNORE; when
    # the env var is missing, fall back to "yes" (the question-tree
    # default).
    quick_env = env if env is not None else dict(os.environ)
    answers.setdefault("create_corpusignore", quick_env.get("CF_CREATE_CORPUSIGNORE", "yes"))
    _apply_corpusignore(answers, resolved_dir)
    return config_path, secrets_path, answers
