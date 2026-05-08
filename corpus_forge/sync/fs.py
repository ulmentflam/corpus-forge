"""Atomic file-write and trash-move utilities for the sync subsystem."""

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def _remove_empty_parents(p: Path) -> None:
    try:
        p.rmdir()
    except OSError:
        return
    _remove_empty_parents(p.parent)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    if not isinstance(path, Path):
        raise TypeError(f"expected Path, got {type(path).__name__}")
    if not isinstance(text, str):
        raise TypeError(f"expected str, got {type(text).__name__}")

    path = path.resolve()

    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        suffix="",
        prefix=path.name + ".tmp.",
        dir=str(path.parent),
    )
    try:
        os.write(fd, text.encode(encoding))
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp_path, str(path))
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if not parent_existed:
            _remove_empty_parents(path.parent)


def move_to_trash(
    src: Path,
    trash_root: Path,
    dataset_name: str,
    host: str,
    rel_path: Path | None = None,
) -> Path:
    src = Path(src)
    trash_root = Path(trash_root)

    if rel_path is not None:
        rel_path = Path(rel_path)
        name_stem = rel_path.stem
        suffix = rel_path.suffix
        dest_parent = trash_root / dataset_name / rel_path.parent
    else:
        name_stem = src.stem
        suffix = src.suffix
        dest_parent = trash_root / dataset_name

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dest_name = f"{name_stem}.deleted-{host}-{ts}{suffix}"
    dest = dest_parent / dest_name

    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(str(src), str(dest))
    except OSError as e:
        if e.errno == 18:  # EXDEV: cross-device link
            shutil.copy2(str(src), str(dest))
            os.unlink(str(src))
        else:
            raise

    return dest


def is_icloud_placeholder(path: Path) -> bool:
    return path.suffix == ".icloud" and path.stat().st_size == 0


def is_dataless(path: Path) -> bool:
    try:
        os.getxattr(str(path), b"com.apple.fileprovider.materialized")
        return True
    except OSError:
        return False
    except Exception:
        return False
