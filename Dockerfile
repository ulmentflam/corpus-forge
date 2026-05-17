# Phase I-14 — corpus-forge container image.
#
# Two-stage build:
#   - ``builder`` installs the package + selected extras into a venv.
#   - ``runtime`` copies the venv onto a slim base image with no
#     build-time tools.
#
# Tags published by ``.github/workflows/release.yml`` on each tag:
#
#   ghcr.io/ulmentflam/corpus-forge:<version>      ← default (sqlite + mcp + hf)
#   ghcr.io/ulmentflam/corpus-forge:<version>-full ← all extras
#   ghcr.io/ulmentflam/corpus-forge:latest          ← alias to most recent <version>
#
# Override the extras set at build time:
#   docker build --build-arg CF_EXTRAS=sqlite,mcp,hf,multi-format -t my-cf .
#
# Run interactively (with the host's ~/.config/corpus-forge mounted):
#   docker run --rm -it -v "$HOME/.config/corpus-forge:/root/.config/corpus-forge" \
#     ghcr.io/ulmentflam/corpus-forge:latest --help

ARG PYTHON_VERSION=3.12
ARG CF_EXTRAS="sqlite,mcp,hf"

FROM python:${PYTHON_VERSION}-slim AS builder

# Build-time deps. ``poppler-utils`` + ``libmagic`` are only needed
# when the user opts into the [ocr] / [multi-format] extras; we add
# them unconditionally in the builder stage so the wheel can compile
# cleanly without a second round-trip.
RUN apt-get update && apt-get install --yes --no-install-recommends \
        build-essential \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Astral) — same provisioning the install.sh uses.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY corpus_forge/ ./corpus_forge/
COPY packaging/ ./packaging/

# Install into a venv at /opt/corpus-forge using the selected extras.
# ``--no-dev`` skips the contributor tool-chain; ``--frozen`` honours
# uv.lock for hash-verified transitives.
ARG CF_EXTRAS
RUN uv venv /opt/corpus-forge --python ${PYTHON_VERSION} \
    && VIRTUAL_ENV=/opt/corpus-forge uv pip install \
        --python /opt/corpus-forge/bin/python \
        ".[${CF_EXTRAS}]"

# ---------------------------------------------------------------------------
# Runtime image: slim base + the prebuilt venv. No build tools, no uv.
# ---------------------------------------------------------------------------

FROM python:${PYTHON_VERSION}-slim AS runtime

# Runtime-only system deps. Kept minimal; users who need the OCR /
# whisper extras can layer on ``poppler-utils`` / ``ffmpeg`` themselves.
RUN apt-get update && apt-get install --yes --no-install-recommends \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/corpus-forge /opt/corpus-forge
ENV PATH="/opt/corpus-forge/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Daemon mode reads CORPUS_FORGE_CONFIG; default points at the
    # mount point the run command bind-mounts.
    CORPUS_FORGE_CONFIG=/root/.config/corpus-forge/config.toml

# Sensible defaults for a long-running container; override at run time.
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "corpus-forge"]
CMD ["--help"]
