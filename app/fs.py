# app/fs.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import time

@dataclass
class StableCheckResult:
    stable: bool
    reason: str

def is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"

def ensure_within_root(root: Path, target: Path) -> bool:
    try:
        root_resolved = root.resolve()
        target_resolved = target.resolve()
        return str(target_resolved).startswith(str(root_resolved) + os.sep) or target_resolved == root_resolved
    except Exception:
        return False

def stable_check(path: Path, logger, wait_seconds: int = 120) -> StableCheckResult:
    try:
        s1 = path.stat()
    except FileNotFoundError:
        return StableCheckResult(False, "file_missing")

    time.sleep(1.0)
    try:
        s2 = path.stat()
    except FileNotFoundError:
        return StableCheckResult(False, "file_missing")

    if (s1.st_size != s2.st_size) or (s1.st_mtime != s2.st_mtime):
        logger.info(f"Stabilitätscheck: Datei instabil, warte {wait_seconds}s: {path}")
        time.sleep(wait_seconds)
        try:
            s3 = path.stat()
        except FileNotFoundError:
            return StableCheckResult(False, "file_missing")

        if (s2.st_size != s3.st_size) or (s2.st_mtime != s3.st_mtime):
            return StableCheckResult(False, "file_unstable_after_wait")

    return StableCheckResult(True, "ok")

def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def move_file(src: Path, dst: Path) -> None:
    safe_mkdir(dst.parent)
    shutil.move(str(src), str(dst))

def next_duplicate_path(dst: Path) -> tuple[Path, int]:
    if not dst.exists():
        return dst, 0

    stem = dst.stem
    suffix = dst.suffix
    n = 1
    while True:
        candidate = dst.with_name(f"{stem}_dup_{n}{suffix}")
        if not candidate.exists():
            return candidate, n
        n += 1