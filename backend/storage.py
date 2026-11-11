"""
Safe reads and writes for the JSON state files (profile, bills, population).

- ``write_json`` is atomic: it writes a temp file in the same directory,
  flushes it to disk, then swaps it in with ``os.replace``. A crash mid-write
  leaves the previous file intact instead of a truncated one.
- ``read_json`` tells "missing" (returns None) apart from "present but
  unreadable" (raises CorruptStateError). Treating a corrupt file as missing
  is how a citizen's profile used to be silently replaced by an empty one.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class CorruptStateError(RuntimeError):
    """A state file exists but cannot be read. It is never overwritten automatically."""

    def __init__(self, path: Path, cause: Exception):
        super().__init__(
            f"Saved data in {path.name} could not be read ({type(cause).__name__}). "
            "It was left untouched so nothing is lost. Repair or move the file aside, then try again."
        )
        self.path = path


def read_json(path: Path) -> Any | None:
    """Parsed JSON, or None when the file does not exist.

    Raises CorruptStateError when the file exists but is not valid UTF-8 JSON
    or cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise CorruptStateError(path, exc) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptStateError(path, exc) from exc


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON atomically. The target is replaced only once complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
