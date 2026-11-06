"""
Append-only audit log.

Every consequential Avatar action is logged here: profile changes, delegate
adds/revocations, votes cast, and citizen overrides. The white paper makes
auditability a core legitimacy property — every Avatar action must be reviewable.

Storage is a simple JSON-lines file (one event per line). Append-only by
convention: we never rewrite history.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
_SCOPE_ID = re.compile(r"[0-9a-f]{16}\Z")
_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar("avatar_audit_scope", default=None)
_root: contextvars.ContextVar[Path | None] = contextvars.ContextVar("avatar_audit_root", default=None)


def push_scope(scope: str, root: Path) -> tuple[contextvars.Token, contextvars.Token]:
    """Write and read this request's audit file, not the shared log."""
    if not _SCOPE_ID.fullmatch(scope):
        raise ValueError("bad audit scope")
    return (_scope.set(scope), _root.set(root))


def reset_scope(token: tuple[contextvars.Token, contextvars.Token]) -> None:
    scope_token, root_token = token
    _scope.reset(scope_token)
    _root.reset(root_token)


def _audit_path() -> Path:
    scope = _scope.get()
    root = _root.get()
    if scope is None or root is None:
        return AUDIT_FILE
    if not _SCOPE_ID.fullmatch(scope):
        raise ValueError("bad audit scope")
    directory = (root / "audits").resolve()
    path = (directory / f"{scope}.jsonl").resolve()
    if path.parent != directory:
        raise ValueError("bad audit scope")
    return path


def log_event(event_type: str, details: dict[str, Any]) -> dict[str, Any]:
    """
    Append an event to the audit log.

    Args:
        event_type: e.g. "profile_created", "delegate_added", "delegate_revoked",
                    "vote_cast", "vote_overridden".
        details: arbitrary JSON-serializable context.

    Returns:
        The stored event record (including its timestamp).
    """
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    event = {
        "timestamp": time.time(),
        "event_type": event_type,
        "details": details,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return event


def read_events(limit: int | None = None) -> list[dict[str, Any]]:
    """
    Read audit events, newest first.

    Args:
        limit: optional cap on number of events returned.

    Returns:
        List of event records, most recent first.
    """
    path = _audit_path()
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    events.reverse()  # newest first
    if limit is not None:
        events = events[:limit]
    return events
