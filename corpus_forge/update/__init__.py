"""Phase I — ``corpus-forge update`` machinery and daily version-check ping.

Two responsibilities live here:

- :func:`detect_channel` figures out *how* the user's installation was
  produced (uv tool, pipx, pip, brew, docker, or source clone) by
  inspecting ``sys.executable`` + a handful of env hints.
  :func:`run_update` then dispatches to the matching upgrade command.
- :func:`check_for_update` is the strictly-anonymous PyPI version ping
  used by ``corpus-forge --version`` (post-Phase-I-11). Caches the
  reply at ``~/.cache/corpus-forge/version-check.json`` for 24h; opt
  out via ``CF_NO_VERSION_CHECK=1``.
"""

from .channels import (
    Channel,
    UpgradeResult,
    detect_channel,
    run_update,
)
from .version_check import VersionCheckResult, check_for_update

__all__ = [
    "Channel",
    "UpgradeResult",
    "VersionCheckResult",
    "check_for_update",
    "detect_channel",
    "run_update",
]
