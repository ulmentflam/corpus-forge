"""Atomic file-write utilities for the sync subsystem."""

from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically via a temp file + fsync + os.replace.

    Algorithm
    ---------
    1. Create parent directories if they do not exist.
    2. Write *text* to ``<path>.tmp.<random>`` in the same directory.
    3. fsync the temp file.
    4. ``os.replace`` the temp file onto *path* (atomic on POSIX).
    5. fsync the parent directory to persist the rename.

    Parameters
    ----------
    path:
        Target file path.
    text:
        Text content to write.
    encoding:
        Text encoding (default ``"utf-8"``).

    Raises
    ------
    OSError:
        If any filesystem operation fails.
    """
    ...  # TODO: implement
