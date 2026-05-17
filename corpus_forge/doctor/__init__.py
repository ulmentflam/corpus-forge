"""Phase I-10 — ``corpus-forge doctor`` diagnostic command.

Walks a fixed list of post-install health checks and emits a single
report. Each check is a pure function returning a :class:`CheckResult`;
the top-level :func:`run_doctor` orchestrates and aggregates.

Doctor is intentionally read-only: it never writes config, never
upgrades anything, never restarts services. The whole point is to be
the "what's broken about my install" oracle without side effects.

Checks (current set, expand by adding to :data:`_CHECKS`):

- Python version meets the floor in ``pyproject.toml`` (>=3.11,<3.14).
- corpus-forge's ``config.toml`` is present and parseable.
- Database schema is at the latest Alembic head.
- Configured Ollama endpoint is reachable (only when a model backend
  points at one).
- ``poppler-utils`` is installed if the OCR extra is enabled.
- ``ffmpeg`` is available if Whisper transcription is enabled.
"""

from .checks import (
    CheckResult,
    CheckStatus,
    DoctorReport,
    run_doctor,
)

__all__ = [
    "CheckResult",
    "CheckStatus",
    "DoctorReport",
    "run_doctor",
]
