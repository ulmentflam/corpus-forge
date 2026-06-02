"""CLI subgroup ``corpus-forge agents`` — T6 + T7 (redirected).

Exposes a single verb, ``init``, that:

1. Detects the project surface (:func:`detect_project_context`).
2. Verifies the project is in the corpus — if not, runs
   ``ingest_one`` + ``backfill_embedder`` (unless ``--no-ingest``).
3. Samples local patterns + queries the corpus retriever.
4. Calls the configured LLM (via ``config.code_enricher``) TWICE — once
   for the private corpus-grounded synthesis and once for the
   sanitized shareable variant.
5. Writes ``.corpus-agents/{AGENTS.md, shareable.md, citations.json,
   meta.json}`` plus — when ``<root>/AGENTS.md`` is absent and
   ``--no-root-write`` was not passed — copies ``shareable.md`` to
   ``<root>/AGENTS.md`` as a starting point for the user.
6. Appends ``.corpus-agents/`` to the project ``.gitignore`` unless
   ``--no-gitignore``.
7. Emits a structured result event under ``--json`` / agent-mode with
   ``paths_written`` / ``sections`` / ``citations``.

Exit codes:
    0  success
    1  user-input error (bad path, conflicting flags)
    2  corpus not ready / no LLM endpoint configured
    3  LLM synthesis failure

CRITICAL safety: ``--force`` applies to ``.corpus-agents/*`` only. It
NEVER overwrites the project-root ``AGENTS.md`` — that's the user's
commitment surface and is left untouched if it already exists.
"""

from __future__ import annotations

import datetime as _dt
import importlib.metadata as _ilm
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from corpus_forge.agents.cross_corpus import (
    CrossCorpusPatterns,
    query_corpus_patterns,
)
from corpus_forge.agents.detector import (
    ProjectContext,
    detect_project_context,
)
from corpus_forge.agents.sampler import LocalPatterns, sample_local_patterns
from corpus_forge.agents.synthesizer import (
    LLMSynthesisError,
    SynthesisResult,
    synthesize,
)
from corpus_forge.agents.writer import (
    WriteResult,
    ensure_gitignore_entry,
    maybe_write_root_agents_md,
    write_corpus_agents_dir,
)
from corpus_forge.ui import agent as ui_agent
from corpus_forge.ui import error as ui_error
from corpus_forge.ui import info as ui_info
from corpus_forge.ui import ok as ui_ok

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Typer sub-app
# ─────────────────────────────────────────────────────────────────────────


agents_app = typer.Typer(
    name="agents",
    help="Corpus-grounded AGENTS.md synthesis (init).",
    add_completion=False,
)


# ─────────────────────────────────────────────────────────────────────────
# Pluggable seams (so tests can patch without touching the CLI body)
# ─────────────────────────────────────────────────────────────────────────


def _load_config():
    from corpus_forge.config import Config  # noqa: PLC0415

    return Config.load()


def _build_llm_callable(config: Any) -> Callable[[str], str]:
    """Build a synchronous ``llm(prompt) -> str`` callable from the
    configured ``[code_enricher]`` block.

    Raises ``LLMSynthesisError`` when the backend is ``"none"``.
    """

    backend = getattr(config.code_enricher, "backend", "none")
    if backend == "none":
        raise LLMSynthesisError(
            "[code_enricher] backend is 'none' — run `corpus-forge setup` and "
            "configure a local or remote LLM endpoint before calling "
            "`corpus-forge agents init`."
        )

    if backend == "local":
        return _build_ollama_llm(
            url=str(config.code_enricher.local_url).rstrip("/"),
            model=config.code_enricher.local_model,
            timeout_s=config.code_enricher.timeout_s,
            temperature=config.code_enricher.temperature,
            api_key=None,
            shape="ollama",
        )

    if backend == "remote":
        api_key = config.resolve_code_enricher_api_key()
        return _build_ollama_llm(
            url=str(config.code_enricher.remote_url).rstrip("/"),
            model=config.code_enricher.remote_model,
            timeout_s=config.code_enricher.timeout_s,
            temperature=config.code_enricher.temperature,
            api_key=api_key,
            shape=config.code_enricher.remote_api_shape,
        )

    raise LLMSynthesisError(f"unsupported code_enricher.backend: {backend!r}")


def _build_ollama_llm(
    *,
    url: str,
    model: str,
    timeout_s: float,
    temperature: float,
    api_key: str | None,
    shape: str,
) -> Callable[[str], str]:
    """Wrap the configured Ollama-shape or OpenAI-shape endpoint as a
    callable that returns plain Markdown.

    The synthesis prompt asks for plain Markdown (NOT JSON), so we send
    the prompt verbatim and read the response string out of the
    envelope.
    """

    from corpus_forge._http import HttpErrors, request_json  # noqa: PLC0415
    from corpus_forge.enrichers.base import (  # noqa: PLC0415
        EnricherResponseError,
        EnricherTimeoutError,
        EnricherUnavailableError,
    )

    errors = HttpErrors(EnricherUnavailableError, EnricherTimeoutError, EnricherResponseError)

    if shape == "openai":
        endpoint = f"{url}/v1/chat/completions"

        def _call(prompt: str) -> str:
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            envelope = request_json(
                "POST",
                endpoint,
                timeout_s=timeout_s,
                errors=errors,
                label="agents-init LLM",
                base_url=url,
                json_body={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "stream": False,
                },
                headers=headers or None,
                required_keys=("choices",),
            )
            choices = envelope.get("choices") or []
            if not choices:
                return ""
            msg = (choices[0] or {}).get("message") or {}
            text = msg.get("content") or ""
            return text if isinstance(text, str) else ""

        return _call

    endpoint = f"{url}/api/generate"

    def _ollama_call(prompt: str) -> str:
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        envelope = request_json(
            "POST",
            endpoint,
            timeout_s=timeout_s,
            errors=errors,
            label="agents-init LLM",
            base_url=url,
            json_body={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature},
            },
            headers=headers or None,
            required_keys=("response",),
            auth_to_unavailable=False,
        )
        text = envelope.get("response") or ""
        return text if isinstance(text, str) else ""

    return _ollama_call


# ─────────────────────────────────────────────────────────────────────────
# Corpus coverage + auto-ingest
# ─────────────────────────────────────────────────────────────────────────


def _project_covered_by_active_dataset(config: Any, project_root: Path) -> bool:
    """Return True if any active ``[[datasets.sources]]`` root contains
    ``project_root`` (or vice versa).

    The match is path-prefix based, normalized via :func:`Path.resolve`.
    """

    target = project_root.resolve()
    for dataset in getattr(config, "datasets", []) or []:
        for source in getattr(dataset, "sources", []) or []:
            for attr in ("root", "vault_root", "projects_root", "storage_root", "chats_root"):
                raw = getattr(source, attr, None)
                if raw is None:
                    continue
                try:
                    candidate = Path(str(raw)).resolve()
                except OSError:
                    continue
                if candidate == target:
                    return True
                # candidate contains target? (the indexed source root is
                # an ancestor of the project root → project is covered)
                try:
                    target.relative_to(candidate)
                    return True
                except ValueError:
                    pass
                # The reverse direction (candidate.relative_to(target)) was
                # removed: it would treat the project as "covered" when only
                # a subdirectory is indexed, which is wrong — synthesis would
                # then run on partial corpus data without triggering ingest.
    return False


def _run_auto_ingest(config: Any, project_root: Path) -> None:
    """Run ``ingest_one`` + ``backfill_embedder`` against ``project_root``.

    Imported lazily so the CLI body stays free of pyrefly/lint
    complaints when ingest/embed extras aren't installed. The
    integration test stubs these symbols out via ``monkeypatch``.
    """

    ui_info("Project not in corpus — running ingest+embed first")
    from corpus_forge import embed as embed_mod  # noqa: PLC0415
    from corpus_forge import ingest as ingest_mod  # noqa: PLC0415

    ingest_mod.ingest_once(config, roots=[project_root])  # type: ignore[attr-defined]
    for embedder_cfg in getattr(config, "embedders", []) or []:
        embed_mod.backfill_embedder(embedder_cfg, None, None)  # type: ignore[call-arg]


# ─────────────────────────────────────────────────────────────────────────
# Default retriever construction
# ─────────────────────────────────────────────────────────────────────────


def _build_default_retriever(config: Any) -> Any | None:
    """Return a :class:`HybridRetriever` against the configured backend
    and primary embedder, or ``None`` when no embedders are configured.
    """

    embedders = getattr(config, "embedders", []) or []
    if not embedders:
        return None
    try:
        # Construct the backend the same way the eval/search code does
        # (``cli._build_eval_retriever``) — Postgres or SQLite, schema
        # migrated, then route the active embedder through
        # ``register_from_config`` so the provider-specific kwargs land.
        from corpus_forge.embedders.registry import (  # noqa: PLC0415
            EmbedderRegistry,
            register_from_config,
        )
        from corpus_forge.retrieval.retriever import HybridRetriever  # noqa: PLC0415

        if config.backend.kind == "sqlite":
            from corpus_forge.backends.sqlite import SQLiteBackend  # noqa: PLC0415

            backend = SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
        else:
            from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415

            backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
        backend.migrate()

        active = next((e for e in embedders if getattr(e, "active", True)), embedders[0])
        reg = EmbedderRegistry()
        embedder = register_from_config(reg, active)
        embedder_id = backend.register_embedder(embedder)
        return HybridRetriever(backend=backend, embedder=embedder, embedder_id=embedder_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(f"agents-init: retriever unavailable ({exc}); skipping cross-corpus query")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Result emission helpers
# ─────────────────────────────────────────────────────────────────────────


def _result_payload(
    *,
    paths_written: list[Path],
    sections: list[str],
    citations: list[Any],
) -> dict[str, Any]:
    """Build the canonical JSON result payload."""

    return {
        "paths_written": [str(p) for p in paths_written],
        "sections": list(sections),
        "citations": [
            {
                "chunk_id": int(c.chunk_id),
                "source_uri": str(c.source_uri),
                "score": float(c.score),
            }
            for c in citations
        ],
    }


def _emit_result(payload: dict[str, Any], *, json_flag: bool) -> None:
    """Emit the result event in whichever mode is active."""

    if json_flag:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if ui_agent.is_agent_mode():
        # Use the same cmd name shape the agent wrapper would generate
        # (space-joined) so agent consumers see one consistent identifier.
        ui_agent.emit("result", cmd="agents init", status="ok", data=payload)
        return
    ui_ok("agents init complete")
    for path in payload["paths_written"]:
        ui_info(f"  wrote {path}")


# ─────────────────────────────────────────────────────────────────────────
# Meta block
# ─────────────────────────────────────────────────────────────────────────


def _build_meta(*, project_root: Path, context: ProjectContext) -> dict[str, Any]:
    """Return the ``meta.json`` payload for the run."""

    try:
        version = _ilm.version("corpus-forge")
    except _ilm.PackageNotFoundError:  # pragma: no cover — dev install
        version = "unknown"
    return {
        "tool": "corpus-forge agents init",
        "version": version,
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "project_root": str(project_root),
        "languages": dict(context.languages),
        "package_managers": list(context.package_managers),
        "test_framework": context.test_framework,
        "build_tool": context.build_tool,
        "license": context.license,
    }


# ─────────────────────────────────────────────────────────────────────────
# The init verb
# ─────────────────────────────────────────────────────────────────────────


@agents_app.command("init")
def agents_init(
    project_root: Annotated[
        Path,
        typer.Option(
            "--project-root",
            "-p",
            help="Path to the project root. Defaults to the current working directory.",
        ),
    ] = Path(),
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            "-o",
            help=(
                "Override the .corpus-agents/ output directory. Defaults to <root>/.corpus-agents/."
            ),
        ),
    ] = None,
    no_root_write: Annotated[
        bool,
        typer.Option(
            "--no-root-write",
            help="Disable auto-write of <root>/AGENTS.md when the file is absent.",
        ),
    ] = False,
    gitignore: Annotated[
        bool,
        typer.Option(
            "--gitignore/--no-gitignore",
            help="Append .corpus-agents/ to <root>/.gitignore (idempotent). Default: on.",
        ),
    ] = True,
    no_ingest: Annotated[
        bool,
        typer.Option(
            "--no-ingest",
            help="Skip auto-ingest+embed when the project root is uncovered by the corpus.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite .corpus-agents/* without prompting. NEVER overwrites root AGENTS.md.",
        ),
    ] = False,
    diff: Annotated[
        bool,
        typer.Option(
            "--diff",
            help="Print a diff vs any existing .corpus-agents/AGENTS.md (advisory).",
        ),
    ] = False,
    json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit a single JSON result object to stdout.",
        ),
    ] = False,
) -> None:
    """Synthesize AGENTS.md grounded in local + cross-corpus patterns.

    Two outputs:
    - ``.corpus-agents/AGENTS.md`` — private, corpus-grounded, with
      chunk_id citations. Gitignored by default.
    - ``.corpus-agents/shareable.md`` — sanitized subset. Copied to
      ``<root>/AGENTS.md`` IFF that file is absent and
      ``--no-root-write`` was not passed.
    """

    # ── 1. Validate inputs ────────────────────────────────────────────
    root = project_root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        ui_error(f"--project-root not a directory: {root}")
        raise typer.Exit(code=1)

    # ── 2. Load config ────────────────────────────────────────────────
    try:
        config = _load_config()
    except Exception as exc:
        ui_error(f"Failed to load corpus-forge config: {exc}")
        raise typer.Exit(code=2) from None

    # ── 3. Coverage / auto-ingest ─────────────────────────────────────
    if not _project_covered_by_active_dataset(config, root):
        if no_ingest:
            ui_error(
                f"Project {root} is not covered by any active dataset source, "
                f"and --no-ingest was passed. Add a [[datasets.sources]] block "
                f"pointing at this directory or drop --no-ingest."
            )
            raise typer.Exit(code=2)
        try:
            _run_auto_ingest(config, root)
        except Exception as exc:
            ui_error(f"Auto-ingest failed: {exc}")
            raise typer.Exit(code=2) from None

    # ── 4. Detect + sample + query ────────────────────────────────────
    context: ProjectContext = detect_project_context(root)
    local: LocalPatterns = sample_local_patterns(context, root)

    retriever = _build_default_retriever(config)
    if retriever is None:
        cross = CrossCorpusPatterns(categories={})
    else:
        try:
            cross = query_corpus_patterns(context, retriever)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(f"agents-init: cross-corpus query failed ({exc}); continuing without it")
            cross = CrossCorpusPatterns(categories={})

    # ── 5. LLM synthesis (two-pass) ───────────────────────────────────
    try:
        llm = _build_llm_callable(config)
    except LLMSynthesisError as exc:
        ui_error(str(exc))
        raise typer.Exit(code=2) from None

    try:
        private: SynthesisResult
        shareable: SynthesisResult
        private, shareable = synthesize(context, local, cross, llm=llm)
    except LLMSynthesisError as exc:
        ui_error(f"LLM synthesis failed: {exc}")
        raise typer.Exit(code=3) from None

    # ── 6. Write outputs ──────────────────────────────────────────────
    corpus_agents_dir = (
        output_dir.expanduser().resolve() if output_dir is not None else (root / ".corpus-agents")
    )
    # Reject up-front when --output-dir points at an existing file —
    # otherwise `write_corpus_agents_dir`'s `mkdir(..., exist_ok=True)`
    # would crash with NotADirectoryError and surface as a traceback.
    if corpus_agents_dir.exists() and not corpus_agents_dir.is_dir():
        ui_error(
            f"--output-dir {corpus_agents_dir!s} exists and is not a directory; "
            "point it at a directory (or a not-yet-existing path)."
        )
        raise typer.Exit(code=1)
    meta = _build_meta(project_root=root, context=context)

    write_result: WriteResult = write_corpus_agents_dir(
        corpus_agents_dir,
        private_md=private.markdown,
        shareable_md=shareable.markdown,
        citations=private.citations,
        meta=meta,
        force=force,
    )
    paths_written: list[Path] = list(write_result.paths_written)

    # Root AGENTS.md — auto-create only when absent + enabled
    root_wrote = maybe_write_root_agents_md(root, shareable.markdown, enabled=not no_root_write)
    if root_wrote:
        paths_written.append(root / "AGENTS.md")

    # .gitignore append
    ensure_gitignore_entry(root, enabled=gitignore)

    # ── 7. Optional diff ──────────────────────────────────────────────
    if diff and write_result.existed_before:
        ui_info(
            "--diff: prior .corpus-agents/AGENTS.md was overwritten; "
            "diff rendering is a future enhancement."
        )

    # Aggregate sections from the private pass (the most informative).
    payload = _result_payload(
        paths_written=paths_written,
        sections=list(private.sections),
        citations=list(private.citations),
    )
    _emit_result(payload, json_flag=json_flag)


__all__ = ["agents_app", "agents_init"]
