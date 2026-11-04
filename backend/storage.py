"""
File persistence primitives.

- Writes are atomic (temp file + os.replace), so a crash mid-write leaves the
  previous version intact instead of a half-written file.
- A corrupt file raises CorruptStateError rather than reading as "missing":
  treating corruption as absence is how a citizen's whole profile gets
  silently overwritten by a fresh one.
- lock(key) serializes read-modify-write cycles. Endpoints run in a thread pool,
  so two requests touching the same profile or bill must not interleave.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class CorruptStateError(RuntimeError):
    """A state file exists but cannot be parsed. Never auto-overwritten."""

    def __init__(self, path: Path, cause: Exception):
        super().__init__(
            f"State file {path} is corrupt ({cause}). It has not been modified; "
            "repair or move it aside to continue."
        )
        self.path = path


def read_json(path: Path) -> Any | None:
    """Parsed JSON, or None if the file does not exist. Raises CorruptStateError."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise CorruptStateError(path, e) from e


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


@contextmanager
def lock(key: str) -> Iterator[None]:
    with _locks_guard:
        lk = _locks.setdefault(key, threading.RLock())
    with lk:
        yield
