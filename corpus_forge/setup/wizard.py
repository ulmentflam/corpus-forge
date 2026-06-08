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

import contextlib
import json as _json
import os
import socket
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from corpus_forge.acceleration import detect_accelerator, recommend_embedder_preset

if TYPE_CHECKING:
    from collections.abc import Mapping

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


# ───────────────────────────────────────────────────────────────────────────
# Lane pinning (RFC fleet-2 item 4)
# ───────────────────────────────────────────────────────────────────────────

#: Minimum heartbeated-host count that makes lane pinning worthwhile.
#: A single-host install has nothing to distribute across, so the wizard
#: only offers the lane prompt once a second host has appeared in the
#: fleet-1 ``corpus.hosts`` registry.
_MULTI_HOST_THRESHOLD = 2


def _parse_lane_csv(raw: str) -> list[str]:
    """Parse a comma-separated lane list (``--embed-lanes a,b``) → names.

    Splits on commas, strips whitespace, and drops empties so ``"a, ,b"``
    and ``"a,b"`` both yield ``["a", "b"]``.  Order-preserving and
    de-duplicated (first occurrence wins) so a config the wizard renders
    is stable.  Name *validity* against ``[[embedders]]`` is enforced by
    ``Config._check_embed_lanes`` at load time — this is just lexing.
    """
    seen: set[str] = set()
    lanes: list[str] = []
    for part in raw.split(","):
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            lanes.append(name)
    return lanes


def _suggest_lanes_from_accelerator(active_embedder_names: list[str]) -> list[str]:
    """Seed a lane suggestion from the host's accelerator probe.

    Intentionally simple (and commented inline): the accelerator probe
    tells us whether this box is a big-VRAM CUDA / Apple-Silicon machine
    (→ suggest the *largest-dimension* lane, the heavy embedder) or a
    CPU-only / low-VRAM box (→ suggest the *smallest-dimension* lane).
    The dimension ranking reuses :func:`recommend_embedder_preset`'s
    ``dimension`` so "big lane" / "small lane" track the same model-size
    intuition the rest of the wizard uses.

    Returns a single-element list (the suggested lane) or ``[]`` when no
    embedders are active.  The operator confirms / overrides the
    suggestion at the prompt — this is a default, not a decision.
    """
    if not active_embedder_names:
        return []
    info = detect_accelerator()
    preset = recommend_embedder_preset(info)
    # Heavy hardware (CUDA / MPS, or the high-VRAM CUDA preset) → the big
    # lane; the CPU preset's full-CPU offload (n_gpu_layers == 0) → the
    # small lane.  We can't read each configured embedder's dimension
    # from a name alone, so the suggestion is the *first* active embedder
    # for the small-hardware case and the *last* for the big-hardware
    # case — config convention puts the heavier embedder later (the
    # wizard emits the auto preset first, the explicit big models after).
    big_hardware = preset.n_gpu_layers != 0
    return [active_embedder_names[-1] if big_hardware else active_embedder_names[0]]


def _count_heartbeated_hosts(answers: dict[str, str], db_path: Path) -> int | None:
    """Return the number of hosts that have heartbeated, or ``None``.

    ``None`` signals "could not tell" — backend unreachable, the
    fleet-1 ``hosts`` table absent, or any probe failure.  The caller
    degrades SILENTLY on ``None`` (no prompt, no error), per the RFC's
    "degrade silently when the backend is unreachable" contract.  A real
    integer (including ``0`` / ``1``) is a successful probe.

    The backend is built read-only (no ``migrate()``): if hosts have
    heartbeated the ``hosts`` table already exists, and a setup-time probe
    should never mutate a shared fleet schema.  Imports are local and the
    whole body is failure-isolated so the lane prompt never turns a
    routine setup into a crash on a box whose Postgres happens to be down
    at install time.
    """
    backend = None
    try:
        backend_kind = answers.get("backend", "sqlite")
        if backend_kind == "sqlite":
            from corpus_forge.backends.sqlite import SQLiteBackend  # noqa: PLC0415

            backend = SQLiteBackend(path=db_path.as_posix(), schema="corpus")
        else:
            from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415

            dsn = answers.get("postgres_dsn") or answers.get(
                "backend_dsn", "postgresql://localhost:5432/corpus_forge"
            )
            backend = PostgresBackend(dsn=dsn, schema="corpus")
        rows = list(backend.list_hosts_with_latest_rate())
        return len(rows)
    except Exception:
        return None
    finally:
        closer = getattr(backend, "close", None)
        if callable(closer):
            # Best-effort close; a failed close must not mask the result.
            with contextlib.suppress(Exception):
                closer()


def _active_embedder_names_from_answers(answers: dict[str, str]) -> list[str]:
    """Best-effort list of the embedder names the rendered config will carry.

    The wizard renders embedders from ``answers["embedder"]`` (and the
    quick path derives a single name from the model id).  Re-derive the
    same names here so the lane prompt lists exactly what the config will
    contain.  Mirrors the name choices in :func:`render_config_toml`.
    """
    embedder = answers.get("embedder", "auto")
    names: list[str] = []
    if embedder == "auto":
        names.append("nomic")
    if embedder in {"st", "both"}:
        names.append("qwen3_8b")
    if embedder in {"openai", "both"}:
        names.append("openai_3l")
    return names


def maybe_prompt_embed_lanes(
    answers: dict[str, str],
    *,
    interactive: bool,
    env: dict[str, str],
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> None:
    """RFC fleet-2 item 4 — offer a lane-pinning prompt, in place.

    Writes ``answers["embed_lanes"]`` (comma-separated) which
    :func:`render_config_toml` turns into an ``[embed] lanes`` block.

    Degrades SILENTLY (no prompt, no error, no ``embed_lanes`` key) when:

    - **non-interactive** without an explicit ``--embed-lanes`` flag
      (the flag lands in ``env["CF_EMBED_LANES"]`` — honoured here);
    - the backend is **unreachable** at setup time;
    - **fewer than 2 hosts** have heartbeated (single-machine install —
      lane pinning is pointless).

    Interactive + 2+ hosts + reachable backend → prompt, seeded from the
    accelerator probe via :func:`_suggest_lanes_from_accelerator`.
    """
    out_stream = stream_out or sys.stdout
    in_stream = stream_in or sys.stdin

    # Non-interactive: only the explicit flag writes lanes; otherwise skip
    # silently (no backend probe — CI installs shouldn't reach for the DB).
    if not interactive:
        flag = env.get("CF_EMBED_LANES", "")
        lanes = _parse_lane_csv(flag)
        if lanes:
            answers["embed_lanes"] = ",".join(lanes)
        return

    # Probe the configured backend for heartbeated hosts.  Read-only and
    # failure-isolated — any error degrades to a silent skip.
    db_path = DEFAULT_CONFIG_DIR / "corpus.db"
    host_count = _count_heartbeated_hosts(answers, db_path)
    if host_count is None or host_count < _MULTI_HOST_THRESHOLD:
        return

    active = _active_embedder_names_from_answers(answers)
    if not active:
        return

    suggested = _suggest_lanes_from_accelerator(active)
    default_csv = ",".join(suggested)

    out_stream.write(
        f"\nFleet detected ({host_count} hosts). This host can be pinned to specific "
        "embedder lanes so it only works those embedders.\n"
        f"  Active embedders: {', '.join(active)}\n"
        f"Embedder lanes for this host (comma-separated; blank = all) "
        f"[default: {default_csv or 'all'}] "
    )
    out_stream.flush()
    raw = in_stream.readline().rstrip("\n").rstrip("\r")
    chosen = _parse_lane_csv(raw) if raw.strip() else suggested
    if chosen:
        answers["embed_lanes"] = ",".join(chosen)


# ───────────────────────────────────────────────────────────────────────────
# Tailscale live-peer picker (RFC fleet-4 item 5)
# ───────────────────────────────────────────────────────────────────────────

#: Default ports appended to a bare ``ts://<peer>`` so the rendered value
#: is connectable without the operator typing the port.  Keyed by a short
#: "kind" the caller passes when offering the picker; the picker renders
#: ``ts://<peer>:<port>`` for that kind.  Postgres speaks 5432; Ollama and
#: most OpenAI-compatible local servers (vLLM defaults aside) listen on
#: 11434, the Ollama port the rest of the wizard already defaults to.
_TAILSCALE_DEFAULT_PORTS: dict[str, int] = {
    "postgres": 5432,  # libpq default
    "ollama": 11434,  # Ollama / OpenAI-compatible local server default
}


def maybe_pick_tailscale_endpoint(
    answers: dict[str, str],
    answer_key: str,
    *,
    kind: str,
    label: str,
    suffix: str = "",
    interactive: bool,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> None:
    """RFC fleet-4 item 5 — offer live tailnet peers for one host field.

    When :func:`corpus_forge.net.tailscale.peers` succeeds, lists the
    *remote* peer names (self filtered out) as numbered choices for the
    field named by ``answer_key`` (e.g. the Postgres DSN host, a remote
    embedder ``base_url``).  Picking one rewrites
    ``answers[answer_key]`` to ``ts://<peer>:<port><suffix>`` — the port
    from :data:`_TAILSCALE_DEFAULT_PORTS` for ``kind`` (commented there),
    and ``suffix`` for any trailing path the field needs (e.g. ``/v1`` on
    an OpenAI-compatible embedder base_url).  Picking ``0`` (or blank)
    keeps whatever the operator already typed.

    Degrades SILENTLY — no prompt, no error, ``answers`` untouched — when:

    - **non-interactive** (the existing ``ts://`` flags already flow
      through ``render_config_toml`` unchanged; no new flag here);
    - :func:`peers` raises :class:`TailscaleUnavailable` (Tailscale
      absent / daemon down — exactly the lanes-prompt posture);
    - :func:`peers` returns no *remote* peers (a one-node tailnet has
      nothing to pick).

    Mirrors :func:`maybe_prompt_embed_lanes`: probe, offer choices,
    degrade silently.
    """
    if not interactive:
        return

    # Local import: net.tailscale shells out to ``tailscale status`` and
    # the wizard is on every CLI cold-start path — keep it out of import.
    from corpus_forge.net.tailscale import TailscaleUnavailable, peers  # noqa: PLC0415

    try:
        all_peers = peers()
    except TailscaleUnavailable:
        return
    remote = [p for p in all_peers if not p.is_self]
    if not remote:
        return

    out_stream = stream_out or sys.stdout
    in_stream = stream_in or sys.stdin
    port = _TAILSCALE_DEFAULT_PORTS.get(kind)

    out_stream.write(f"\nTailscale peers available for {label}:\n")
    out_stream.write("  0) keep current\n")
    for idx, peer in enumerate(remote, start=1):
        marker = " (offline)" if not peer.online else ""
        out_stream.write(f"  {idx}) {peer.name}{marker}\n")
    out_stream.write("Pick a peer by number (blank = keep current) [0] ")
    out_stream.flush()

    raw = in_stream.readline().rstrip("\n").rstrip("\r").strip()
    if not raw:
        return
    try:
        choice = int(raw)
    except ValueError:
        return
    if choice < 1 or choice > len(remote):
        return

    picked = remote[choice - 1].name
    endpoint = f"ts://{picked}:{port}" if port else f"ts://{picked}"
    answers[answer_key] = f"{endpoint}{suffix}"


def maybe_pick_tailscale_endpoints(
    answers: dict[str, str],
    *,
    interactive: bool,
    stream_in: IO[str] | None = None,
    stream_out: IO[str] | None = None,
) -> None:
    """Offer the live-peer picker for every host field the full wizard set.

    Walks the answer fields that name a remote host — the Postgres DSN
    (when ``backend=postgres``) and the OpenAI-compatible embedder
    ``base_url`` (when an OpenAI embedder is configured) — and offers
    :func:`maybe_pick_tailscale_endpoint` for each.  Each call degrades
    silently when Tailscale is absent, so on a non-tailnet box this is a
    no-op that leaves ``answers`` byte-identical.
    """
    if answers.get("backend") == "postgres":
        maybe_pick_tailscale_endpoint(
            answers,
            "postgres_dsn",
            kind="postgres",
            label="the PostgreSQL host",
            interactive=interactive,
            stream_in=stream_in,
            stream_out=stream_out,
        )
    if answers.get("embedder") in {"openai", "both"}:
        # OpenAI-compatible servers (Ollama / vLLM) expose ``/v1`` — the
        # base_url needs that path suffix appended to the ts:// host.
        maybe_pick_tailscale_endpoint(
            answers,
            "openai_base_url",
            kind="ollama",
            label="the remote embedder base_url",
            suffix="/v1",
            interactive=interactive,
            stream_in=stream_in,
            stream_out=stream_out,
        )


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

    # ── [embed] (lane pinning — RFC fleet-2 item 4) ────────────────────
    # Only emitted when the lane-prompt produced a non-empty selection
    # (multi-host fleet + a reachable backend, OR an explicit
    # ``--embed-lanes`` flag in non-interactive mode).  Absent block ⇒
    # all active embedders, today's behaviour.
    embed_lanes = _parse_lane_csv(answers.get("embed_lanes", ""))
    if embed_lanes:
        rendered = ", ".join(_quote_toml_str(lane) for lane in embed_lanes)
        out.append("[embed]")
        out.append(f"lanes = [{rendered}]")
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
    # RFC fleet-4 item 5 — offer live tailnet peers for the host fields
    # (Postgres DSN, remote embedder base_url) before the config is
    # rendered.  Degrades silently when Tailscale is absent.
    maybe_pick_tailscale_endpoints(
        answers,
        interactive=True,
        stream_in=stream_in,
        stream_out=stream_out,
    )
    # RFC fleet-2 item 4 — offer lane pinning before the config is
    # rendered (it writes ``answers["embed_lanes"]``).  Degrades silently
    # off a multi-host fleet / reachable backend.
    maybe_prompt_embed_lanes(
        answers,
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
    resolved_env = env or dict(os.environ)
    answers = _collect_answers(
        questions,
        interactive=False,
        env=resolved_env,
    )
    # RFC fleet-2 item 4 — ``CF_EMBED_LANES`` (from ``--embed-lanes a,b``)
    # writes the lanes list; absent ⇒ no ``[embed]`` block (all lanes).
    maybe_prompt_embed_lanes(answers, interactive=False, env=resolved_env)
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


# ───────────────────────────────────────────────────────────────────────────
# Join flow (RFC fleet-3 item 5)
# ───────────────────────────────────────────────────────────────────────────
#
# ``corpus-forge setup --join <dsn>`` takes a fresh machine from
# *installed* to *registered host with the fleet's shared config* in one
# command.  The flow:
#
# 1. Connect to the shared Postgres and verify the corpus schema is
#    present (a primary host has run ``migrate``).  We NEVER auto-migrate
#    on join — the fleet's primary owns the schema lifecycle.
# 2. Register this host in ``corpus.hosts`` (fleet-1 ``upsert_host``)
#    with the accelerator probe payload.
# 3. Render a minimal local ``config.toml`` (backend postgres + the join
#    DSN, ``[daemon]``, ``datasets = []``) merged with the published
#    shared scope via ``merge_shared_scope`` — EXCEPT shared datasets,
#    which arrive name/kind-only (no ``sources``) and would fail
#    ``Config.validate_dataset_sources``.  Those land as COMMENTED-OUT
#    ``[[datasets]]`` blocks the operator un-comments after pointing each
#    at a local source root.
# 4. Record the pulled version locally (federation state file).
#
# The host-id derivation here mirrors :meth:`Config.host_id`'s fallback
# (``socket.gethostname()``, persisted to ``<config_dir>/host_id``) —
# but no config exists yet at join time, so we resolve it minimally and
# seed the same ``host_id`` file the later loaded ``Config.host_id()``
# will read, keeping the id stable across the join → first-run boundary.


class JoinError(RuntimeError):
    """A recoverable join failure the CLI surfaces as a clean exit-1.

    Carries an operator-facing message (no traceback) — raised for an
    unreachable DSN, a missing corpus schema, or an existing config the
    non-interactive path refuses to clobber.
    """


def _resolve_join_host_id(config_dir: Path) -> str:
    """Resolve this host's id the way :meth:`Config.host_id` will, sans config.

    At join time no ``config.toml`` exists yet, so we can't call
    :meth:`Config.host_id`.  Reimplement its *fallback* branch (the one
    that fires when ``daemon.host_id`` is empty): read the persisted
    ``host_id`` file if present, else use ``socket.gethostname()`` and
    persist it.  We seed the SAME ``<config_dir>/host_id`` file the
    later-loaded config's ``host_id()`` reads, so the id stays stable
    across the join → first ``ingest`` / ``embed`` boundary instead of
    being re-derived (and a host registered under one id then
    heartbeating under another would orphan its ``corpus.hosts`` row).
    """
    host_id_path = config_dir / "host_id"
    if host_id_path.exists():
        existing = host_id_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    hostname = socket.gethostname()
    host_id_path.parent.mkdir(parents=True, exist_ok=True)
    host_id_path.write_text(hostname, encoding="utf-8")
    return hostname


def _connect_and_verify_schema(dsn: str) -> Any:
    """Connect to ``dsn`` and verify the corpus schema is present.

    Returns a live :class:`PostgresBackend`.  Raises :class:`JoinError`
    with an operator-facing hint when the DSN is unreachable or the
    ``corpus.hosts`` table is absent (no primary host has migrated).
    NEVER migrates — the fleet's primary owns the schema.
    """
    from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415

    try:
        backend = PostgresBackend(dsn=dsn, schema="corpus")
    except Exception as exc:  # connection / pool warm-up failure
        raise JoinError(
            f"could not connect to {dsn!r}: {exc}. Is the DSN right? "
            "Is the shared Postgres reachable from this host?"
        ) from exc

    # Verify the schema exists WITHOUT migrating: probe the fleet-1
    # ``corpus.hosts`` table directly (a primary host running ``migrate``
    # creates it).  ``to_regclass`` returns NULL when the table is
    # absent — cheaper and more portable than scraping information_schema.
    try:
        rows = backend._execute("SELECT to_regclass('corpus.hosts') AS reg")
    except Exception as exc:
        _close_join_backend(backend)
        raise JoinError(
            f"could not query {dsn!r}: {exc}. Is the DSN right? "
            "Is the shared Postgres reachable from this host?"
        ) from exc
    present = bool(rows) and rows[0].get("reg") is not None
    if not present:
        _close_join_backend(backend)
        raise JoinError(
            "the corpus schema is missing on the target database "
            "(corpus.hosts not found). Has a primary host run "
            "`corpus-forge migrate`? Join never migrates the shared schema."
        )
    return backend


def _close_join_backend(backend: Any) -> None:
    """Best-effort backend close (mirrors the federation verbs)."""
    closer = getattr(backend, "close", None)
    if callable(closer):
        with contextlib.suppress(Exception):
            closer()


def _render_skeleton_join_config(dsn: str) -> str:
    """Render the minimal local skeleton the shared scope merges onto.

    Backend postgres + the join DSN, an empty ``[daemon]`` block (daemon
    defaults), and a root-scope ``datasets = []`` (no local datasets yet;
    shared datasets land as commented blocks the operator fills in).  The
    ``datasets = []`` bare key MUST precede any ``[section]`` header —
    TOML binds bare keys to the most-recent table.
    """
    out: list[str] = [
        "# Generated by `corpus-forge setup --join`. Edit freely after joining.",
        "# Shared scope (embedders / retrieval / model choices) was pulled from",
        "# the fleet's published config; local scope (DSN, sources) is yours.",
        "",
        # Root-scope key first (before any [table]).
        "datasets = []",
        "",
        "[backend]",
        'kind = "postgres"',
        f"dsn  = {_quote_toml_str(dsn)}",
        "",
        "[daemon]",
        "",
    ]
    return "\n".join(out) + "\n"


def _render_commented_datasets(shared_datasets: list[dict[str, object]]) -> str:
    """Render fleet datasets as COMMENTED-OUT ``[[datasets]]`` blocks.

    THE VALIDATION TRAP: a shared dataset carries ``name`` / ``kind`` but
    no ``sources`` (each machine ingests its own directories — that's a
    feature).  A live ``[[datasets]]`` block with no ``sources`` fails
    :meth:`Config.validate_dataset_sources` ("must have at least one
    source"), so the merged file would not load.  We therefore emit each
    shared dataset as a commented block the operator un-comments after
    adding a local ``[[datasets.sources]]`` entry.  Returns ``""`` when
    no shared datasets were published.
    """
    if not shared_datasets:
        return ""
    out: list[str] = [
        "",
        "# ── fleet datasets (awaiting local sources) ─────────────────────────",
        "# These dataset names/kinds are shared across the fleet. Each machine",
        "# ingests its OWN directories, so no sources were pulled. Uncomment a",
        "# block and add your local [[datasets.sources]] entry to activate it.",
    ]
    for ds in shared_datasets:
        name = ds.get("name")
        kind = ds.get("kind", "text")
        if not isinstance(name, str) or not name:
            continue
        kind_str = kind if isinstance(kind, str) and kind else "text"
        out.append("")
        out.append("# fleet dataset — uncomment and add your local [[datasets.sources]] block")
        out.append("# [[datasets]]")
        out.append(f"# name = {_quote_toml_str(name)}")
        out.append(f"# kind = {_quote_toml_str(kind_str)}")
        out.append(
            '# sources = [{plugin = "filesystem", root = "~/path/to/data", chunker = "markdown"}]'
        )
    return "\n".join(out) + "\n"


def render_join_config(dsn: str, shared_body: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    """Render the full join ``config.toml`` text + the awaiting-source names.

    ``shared_body`` is the published shared-scope dict (or ``None`` when
    nothing is published yet).  Shared datasets are split out and
    rendered as commented blocks (the validation trap); every other
    shared key (embedders / retrieval / model choices) is merged LIVE
    onto the skeleton via :func:`merge_shared_scope`.  The returned text
    is guaranteed to load via :meth:`Config.load` (verified by the
    skeleton-render unit test).

    Returns ``(config_text, awaiting_source_dataset_names)``.
    """
    from corpus_forge.config_scope import merge_shared_scope  # noqa: PLC0415

    skeleton = _render_skeleton_join_config(dsn)

    # Pull datasets out of the live merge — they would arrive sources-less
    # and break Config validation. Everything else merges live.
    shared_datasets: list[dict[str, object]] = []
    live_shared: dict[str, Any] = {}
    if shared_body:
        for key, value in shared_body.items():
            if key == "datasets" and isinstance(value, list):
                shared_datasets = [d for d in value if isinstance(d, dict)]
            else:
                live_shared[key] = value

    merged = merge_shared_scope(skeleton, live_shared) if live_shared else skeleton
    commented = _render_commented_datasets(shared_datasets)
    config_text = merged + commented if commented else merged

    awaiting = [d["name"] for d in shared_datasets if isinstance(d.get("name"), str) and d["name"]]
    return config_text, awaiting


#: Post-join "next steps" — distinct from :data:`NEXT_STEPS` (fresh
#: install) because the join host's first move is filling in local
#: sources for the fleet datasets, then ingesting + calibrating, and
#: it pulls (not publishes) shared config on a cadence.
JOIN_NEXT_STEPS: tuple[str, ...] = (
    "Next steps:",
    "  1. Edit config.toml — uncomment each fleet [[datasets]] block and add a",
    "     local [[datasets.sources]] entry pointing at this machine's data.",
    "  corpus-forge ingest --once    # one-shot sync of the configured roots",
    "  corpus-forge bench embed --all  # calibrate this machine's embedder "
    "throughput (see `models list`)",
    "  corpus-forge config pull --apply  # pull fleet shared-config updates on a cadence",
)


def render_join_next_steps(awaiting: list[str]) -> str:
    """Return the post-join next-steps block, naming datasets awaiting sources."""
    lines = list(JOIN_NEXT_STEPS)
    if awaiting:
        lines.append("  Datasets awaiting a local source: " + ", ".join(awaiting))
    return "\n".join(lines) + "\n"


def run_join(
    dsn: str,
    *,
    config_dir: Path | None = None,
    interactive: bool = True,
    stream_out: IO[str] | None = None,
) -> tuple[Path, list[str]]:
    """Join an existing corpus-forge fleet (RFC fleet-3 item 5).

    Connects to ``dsn``, verifies the corpus schema, registers this host
    in ``corpus.hosts``, renders a minimal local ``config.toml`` merged
    with the fleet's published shared scope, and records the pulled
    version locally.

    Args:
        dsn: libpq DSN of the shared Postgres (the fleet's primary owns
            the schema; join NEVER migrates).
        config_dir: Output directory; defaults to ``~/.config/corpus-forge``.
        interactive: When True, an existing ``config.toml`` triggers a
            :class:`Confirm.ask` overwrite prompt (backup to
            ``config.toml.bak`` on yes).  When False, an existing config
            is refused with :class:`JoinError` (no ``--force`` in this
            slice).
        stream_out: Stream injection for testing the printed output.

    Returns:
        ``(config_path, awaiting_source_dataset_names)``.

    Raises:
        JoinError: unreachable DSN, missing schema, or refused overwrite.
    """
    from corpus_forge.admin.federation import write_last_pulled_version  # noqa: PLC0415

    out_stream = stream_out or sys.stdout
    resolved_dir = config_dir or DEFAULT_CONFIG_DIR
    resolved_dir.mkdir(parents=True, exist_ok=True)
    config_path = resolved_dir / "config.toml"

    # ── Safety: existing config ────────────────────────────────────────
    if config_path.exists():
        if not interactive:
            raise JoinError(
                f"{config_path} already exists; refusing to overwrite in "
                "non-interactive mode. Move it aside (or re-run interactively "
                "to confirm) and try again."
            )
        from corpus_forge.ui.prompts import Confirm  # noqa: PLC0415

        if not Confirm.ask(
            f"{config_path} already exists. Overwrite (a backup is written to config.toml.bak)?",
            default=False,
        ):
            raise JoinError("join aborted — existing config.toml left untouched.")
        backup_path = config_path.with_name(config_path.name + ".bak")
        backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        out_stream.write(
            f"[corpus-forge setup --join] backed up existing config to {backup_path}\n"
        )

    # ── 1. Connect + verify schema (never migrate) ─────────────────────
    backend = _connect_and_verify_schema(dsn)
    try:
        # ── 2. Register host (fleet-1 upsert) ──────────────────────────
        import platform  # noqa: PLC0415

        from corpus_forge.telemetry_registry import accelerator_payload  # noqa: PLC0415

        host_id = _resolve_join_host_id(resolved_dir)
        try:
            backend.upsert_host(
                host_id=host_id,
                hostname=socket.gethostname(),
                os=platform.platform(),
                accelerator=accelerator_payload(),
            )
        except Exception as exc:
            raise JoinError(f"could not register this host in corpus.hosts: {exc}") from exc

        # ── 3. Fetch published shared scope + render config ────────────
        fetched = backend.get_shared_config()
    finally:
        _close_join_backend(backend)

    if fetched is None:
        published_version = 0
        shared_body: dict[str, Any] | None = None
        out_stream.write(
            "[corpus-forge setup --join] no shared config published yet — "
            "rendering the skeleton with live local parts only.\n"
        )
    else:
        published_version, shared_body = int(fetched[0]), fetched[1]

    config_text, awaiting = render_join_config(dsn, shared_body)
    config_path.write_text(config_text, encoding="utf-8")

    # ── 4. Record the pulled version locally ───────────────────────────
    # ``write_last_pulled_version`` writes beside the config resolved via
    # ``resolve_config_path`` (CORPUS_FORGE_CONFIG-aware), which is the
    # same file we just wrote — tests pin CORPUS_FORGE_CONFIG to it.
    write_last_pulled_version(published_version)

    # ── 5. Next-steps print ────────────────────────────────────────────
    if awaiting:
        out_stream.write(
            "[corpus-forge setup --join] fleet datasets awaiting local sources: "
            + ", ".join(awaiting)
            + "\n"
        )
    out_stream.write(render_join_next_steps(awaiting))
    return config_path, awaiting
