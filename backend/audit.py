"""
Append-only, tamper-evident audit log.

Every consequential Avatar action is logged here: profile changes, delegate
adds/revocations, votes cast, and citizen overrides. The white paper makes
auditability a core legitimacy property — every Avatar action must be reviewable.

Storage is JSON lines. Each event carries `prev_hash` and `hash`, where
hash = sha256(prev_hash + canonical JSON of {timestamp, event_type, details}).
Editing, deleting, or reordering any past event breaks the chain from that point,
and verify_chain() reports where. (This makes tampering *evident*, not
impossible: someone with file access can rewrite the whole chain. Publishing the
latest hash elsewhere — a "checkpoint" — closes that gap.)
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIT_FILE = DATA_DIR / "audit.jsonl"

GENESIS = "0" * 64

_lock = threading.Lock()
_last_hash: dict[Path, str] = {}  # cache per file; tests swap AUDIT_FILE


def _digest(prev_hash: str, timestamp: float, event_type: str, details: dict[str, Any]) -> str:
    body = json.dumps(
        {"timestamp": timestamp, "event_type": event_type, "details": details},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _read_raw() -> list[dict[str, Any]]:
    if not AUDIT_FILE.exists():
        return []
    events = []
    with AUDIT_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"event_type": "_unparseable", "raw": line})
    return events


def _tail_hash() -> str:
    if AUDIT_FILE not in _last_hash:
        last = GENESIS
        for e in _read_raw():
            if isinstance(e.get("hash"), str):
                last = e["hash"]
        _last_hash[AUDIT_FILE] = last
    return _last_hash[AUDIT_FILE]


def log_event(event_type: str, details: dict[str, Any]) -> dict[str, Any]:
    """Append an event to the audit log and return the stored record."""
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        prev = _tail_hash()
        ts = time.time()
        event = {
            "timestamp": ts,
            "event_type": event_type,
            "details": details,
            "prev_hash": prev,
            "hash": _digest(prev, ts, event_type, details),
        }
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        _last_hash[AUDIT_FILE] = event["hash"]
        return event


def read_events(limit: int | None = None) -> list[dict[str, Any]]:
    """Audit events, newest first."""
    events = [e for e in _read_raw() if e.get("event_type") != "_unparseable"]
    events.reverse()
    return events[:limit] if limit is not None else events


def verify_chain() -> dict[str, Any]:
    """Recompute the hash chain.

    Events written before hashing existed (no `hash` field) are counted as
    `legacy` and skipped; the chain starts at the first hashed event.

    Returns {"ok", "events", "verified", "legacy", "broken_at", "reason"} where
    broken_at is the 0-based line index of the first bad event (or None).
    """
    raw = _read_raw()
    prev = GENESIS
    verified = legacy = 0
    started = False
    for i, e in enumerate(raw):
        if e.get("event_type") == "_unparseable":
            return _bad(raw, verified, legacy, i, "line is not valid JSON")
        if "hash" not in e:
            if started:
                return _bad(raw, verified, legacy, i, "unhashed event inside the chain")
            legacy += 1
            continue
        started = True
        if e.get("prev_hash") != prev:
            return _bad(raw, verified, legacy, i, "prev_hash does not match the previous event")
        expected = _digest(prev, e.get("timestamp"), e.get("event_type"), e.get("details", {}))
        if e["hash"] != expected:
            return _bad(raw, verified, legacy, i, "event content does not match its hash")
        prev = e["hash"]
        verified += 1
    return {"ok": True, "events": len(raw), "verified": verified, "legacy": legacy,
            "broken_at": None, "reason": "", "head": prev}


def _bad(raw, verified, legacy, i, reason) -> dict[str, Any]:
    return {"ok": False, "events": len(raw), "verified": verified, "legacy": legacy,
            "broken_at": i, "reason": reason, "head": None}
