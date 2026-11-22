"""
Append-only audit log.

Every consequential Avatar action is logged here: profile changes, delegate
adds/revocations, votes cast, and citizen overrides. The white paper makes
auditability a core legitimacy property — every Avatar action must be reviewable.

Storage is a JSON-lines file (one event per line). Each event carries
``prev_hash`` and ``hash``, where

    hash = sha256(prev_hash + canonical JSON of {timestamp, event_type, details})

so the events form a chain. Editing, deleting, inserting, or reordering a past
event breaks the chain from that point, and ``verify_chain`` reports where.
This makes tampering *evident*, not impossible: someone who can write the file
can rebuild the whole chain. Noting the ``head`` hash somewhere else from time
to time (a checkpoint) closes that gap, since a rebuilt chain ends in a
different head. Events written before chaining existed have no hash; they are
counted as legacy and the chain starts after them.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIT_FILE = DATA_DIR / "audit.jsonl"
_SCOPE_ID = re.compile(r"[0-9a-f]{16}\Z")
_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar("avatar_audit_scope", default=None)
_root: contextvars.ContextVar[Path | None] = contextvars.ContextVar("avatar_audit_root", default=None)

GENESIS = "0" * 64
_append_lock = threading.Lock()
# path -> (file size when last seen, hash of its last event). A size change
# made outside log_event forces a rescan, so a stale tail is never chained onto.
_tails: dict[str, tuple[int, str]] = {}


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


def _digest(prev_hash: str, timestamp: Any, event_type: Any, details: Any) -> str:
    body = json.dumps(
        {"timestamp": timestamp, "event_type": event_type, "details": details},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _scan_tail(path: Path) -> str:
    """Hash of the last chained event in the file, or GENESIS."""
    last = GENESIS
    if not path.exists():
        return last
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = event.get("hash") if isinstance(event, dict) else None
            if isinstance(value, str) and len(value) == 64:
                last = value
    return last


def _tail_hash(path: Path) -> str:
    size = path.stat().st_size if path.exists() else 0
    cached = _tails.get(str(path))
    if cached is not None and cached[0] == size:
        return cached[1]
    return _scan_tail(path)


def log_event(event_type: str, details: dict[str, Any]) -> dict[str, Any]:
    """
    Append an event to the audit log, chained to the previous one.

    Args:
        event_type: e.g. "profile_created", "delegate_added", "delegate_revoked",
                    "vote_cast", "vote_overridden".
        details: arbitrary JSON-serializable context.

    Returns:
        The stored event record (timestamp, prev_hash, and hash included).
    """
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    with _append_lock:
        prev = _tail_hash(path)
        timestamp = time.time()
        event = {
            "timestamp": timestamp,
            "event_type": event_type,
            "details": details,
            "prev_hash": prev,
            "hash": _digest(prev, timestamp, event_type, details),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        _tails[str(path)] = (path.stat().st_size, event["hash"])
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return event


def verify_chain() -> dict[str, Any]:
    """Recompute this audit log's hash chain.

    Returns:
        ok: True when every chained event checks out.
        events: lines in the file. verified: chained events that check out.
        legacy: events from before chaining, at the start of the file.
        broken_at: 1-based line number of the first bad event, or None.
        reason: why that line is bad. head: the last verified hash.
    """
    path = _audit_path()
    result: dict[str, Any] = {
        "ok": True, "events": 0, "verified": 0, "legacy": 0,
        "broken_at": None, "reason": "", "head": GENESIS,
    }
    if not path.exists():
        return result

    def broken(line_no: int, reason: str) -> dict[str, Any]:
        result.update(ok=False, broken_at=line_no, reason=reason)
        return result

    prev = GENESIS
    chained = False
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            result["events"] += 1
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return broken(line_no, "line is not valid JSON")
            if not isinstance(event, dict):
                return broken(line_no, "line is not an event object")
            if "hash" not in event:
                if chained:
                    return broken(line_no, "unchained event inside the chain")
                result["legacy"] += 1
                continue
            chained = True
            if event.get("prev_hash") != prev:
                return broken(line_no, "previous-hash link does not match the event before it")
            expected = _digest(prev, event.get("timestamp"), event.get("event_type"), event.get("details"))
            if event.get("hash") != expected:
                return broken(line_no, "event content does not match its hash")
            prev = expected
            result["verified"] += 1
            result["head"] = prev
    return result


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
