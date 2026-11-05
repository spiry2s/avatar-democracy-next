"""Invite tokens for a closed demo.

This module is a local admin tool and a store. It is not an HTTP API.
``python -m backend.invites`` prints a link, lists ids, and revokes tokens.
Nothing in here is mounted on the FastAPI app.

The raw token is returned once by ``create_invite`` and is never written to
the database. The database stores HMAC-SHA256(token) plus a label, timestamps,
a revoked flag, and budgets. Comparison uses ``hmac.compare_digest``.

AVATAR_INVITE_MODE is enforced in ``main.guard_api_request``, in the same
block as the shared per-IP spending limit. This module only stores, checks,
and reserves.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlencode

# 32 bytes from the OS CSPRNG. token_urlsafe encodes that; it is not 32 characters.
TOKEN_BYTES = 32
COOKIE_NAME = "avatar_invite"
DB_NAME = "invites.sqlite"
KEY_NAME = "invite-hmac.key"
LOG_NAME = "invite-usage.jsonl"
DEFAULT_BILL_BUDGET = 30
DEFAULT_BILL_DAILY = 10
DEFAULT_LLM_BUDGET = 60
DEFAULT_TTL_HOURS = 336
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_MAX_TTL_SECONDS = 10 * 366 * 24 * 3600
_MAX_EXTEND_HOURS = 24 * 366 * 10
_ID_RE = re.compile(r"[0-9a-f]{16}\Z")
_ISSUED_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,200}\Z")
_PRESENTED_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
_LABEL_BAD = re.compile(r"[\x00-\x1f\x7f]")
_DUMMY_HASH = hashlib.sha256(b"avatar-invite-dummy").hexdigest()
# Used only to spend the same HMAC work when the store has no key yet.
_UNKEYED = hashlib.sha256(b"avatar-invite-unkeyed").digest()
_SECRET_LOG_KEYS = frozenset({
    "token",
    "token_hash",
    "cookie",
    "authorization",
    "api_key",
    "secret",
    "password",
})
_TOKEN_IN_TEXT = re.compile(r"(?i)([?&#]token=)[^&#\s\"']+")
_COOKIE_IN_TEXT = re.compile(rf"(?i)({re.escape(COOKIE_NAME)}=)[^;\s\"']+")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DENIED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Closed demo</title>
</head>
<body>
<p>This demo is closed.</p>
</body>
</html>
"""

_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}


class InvitePathError(ValueError):
    """The configured data directory is not a safe place to write."""


@dataclass(frozen=True)
class Invite:
    """Public invite record. No raw token and no hash."""

    id: str
    label: str
    created_at: float
    expires_at: float
    revoked: bool
    bill_budget: int
    bill_daily_budget: int
    llm_budget: int
    bills_used: int
    bills_used_today: int
    bills_day: str
    llm_calls_used: int


@dataclass(frozen=True)
class IssuedInvite:
    """Return value of create. ``token`` is shown once and then discarded."""

    invite: Invite
    token: str
    link: str


def invite_mode_enabled() -> bool:
    """True when AVATAR_INVITE_MODE is 1, true, yes, or on."""
    raw = os.environ.get("AVATAR_INVITE_MODE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def default_bill_budget() -> int:
    return _positive_int_env("AVATAR_INVITE_BILL_BUDGET", DEFAULT_BILL_BUDGET)


def default_bill_daily() -> int:
    return _positive_int_env("AVATAR_INVITE_BILL_DAILY", DEFAULT_BILL_DAILY)


def default_llm_budget() -> int:
    return _positive_int_env("AVATAR_INVITE_LLM_BUDGET", DEFAULT_LLM_BUDGET)


def default_ttl_seconds() -> int:
    hours = _positive_int_env("AVATAR_INVITE_TTL_HOURS", DEFAULT_TTL_HOURS)
    return hours * 3600


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_data_dir(raw: str | None = None, *, project_root: Path | None = None) -> Path:
    """Directory for the SQLite file, HMAC key, and usage log.

    A blank value is ``<project>/data``. Relative paths stay inside the
    project after resolving symlinks. Any ``..`` segment is rejected, including
    in an absolute path. An absolute path with no ``..`` is allowed so the
    store can live outside the repository.
    """
    root = (project_root if project_root is not None else PROJECT_ROOT).resolve()
    if raw is None:
        raw = os.environ.get("AVATAR_INVITE_DATA_DIR", "")
    text = (raw or "").strip()
    if "\x00" in text:
        raise InvitePathError("data dir rejected")
    if not text:
        return (root / "data").resolve()
    normalized = text.replace("\\", "/")
    if any(part == ".." for part in normalized.split("/")):
        raise InvitePathError("data dir rejected")
    candidate = Path(text)
    relative = not candidate.is_absolute()
    if relative:
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    if relative and not _is_relative_to(resolved, root):
        raise InvitePathError("data dir rejected")
    return resolved


def _child(data_dir: Path, name: str) -> Path:
    if name != Path(name).name or name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise InvitePathError("data dir rejected")
    path = (data_dir / name).resolve()
    if path.parent != data_dir.resolve():
        raise InvitePathError("data dir rejected")
    return path


def _tighten(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def _ensure_key(data_dir: Path) -> bytes:
    path = _child(data_dir, KEY_NAME)
    if path.exists():
        data = path.read_bytes()
        if len(data) != 32:
            raise InvitePathError("data dir rejected")
        _tighten(path)
        return data
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        data = path.read_bytes()
        if len(data) != 32:
            raise InvitePathError("data dir rejected")
        return data
    try:
        os.write(fd, secrets.token_bytes(32))
    finally:
        os.close(fd)
    _tighten(path)
    data = path.read_bytes()
    if len(data) != 32:
        raise InvitePathError("data dir rejected")
    return data


def _load_key_bytes() -> bytes | None:
    data_dir = resolve_data_dir()
    path = _child(data_dir, KEY_NAME)
    if not path.exists():
        return None
    data = path.read_bytes()
    if len(data) != 32:
        return None
    return data


def _schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invites (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            bill_budget INTEGER NOT NULL,
            bill_daily_budget INTEGER NOT NULL,
            llm_budget INTEGER NOT NULL,
            bills_used INTEGER NOT NULL DEFAULT 0,
            bills_used_today INTEGER NOT NULL DEFAULT 0,
            bills_day TEXT NOT NULL DEFAULT '',
            llm_calls_used INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(invites)")}
    if "bill_daily_budget" not in columns:
        conn.execute(
            "ALTER TABLE invites ADD COLUMN bill_daily_budget INTEGER NOT NULL DEFAULT "
            + str(int(DEFAULT_BILL_DAILY))
        )
    if "bills_used_today" not in columns:
        conn.execute(
            "ALTER TABLE invites ADD COLUMN bills_used_today INTEGER NOT NULL DEFAULT 0"
        )
    if "bills_day" not in columns:
        conn.execute(
            "ALTER TABLE invites ADD COLUMN bills_day TEXT NOT NULL DEFAULT ''"
        )


def _open_connection(path: Path) -> sqlite3.Connection:
    key = str(path)
    existing = _connections.get(key)
    if existing is not None:
        return existing
    conn = sqlite3.connect(key, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _schema(conn)
    _tighten(path)
    _connections[key] = conn
    return conn


def _connect_write() -> sqlite3.Connection:
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _ensure_key(data_dir)
    return _open_connection(_child(data_dir, DB_NAME))


def _connect_readonly() -> sqlite3.Connection | None:
    data_dir = resolve_data_dir()
    path = _child(data_dir, DB_NAME)
    cached = _connections.get(str(path))
    if cached is not None:
        return cached
    if not path.exists():
        return None
    return _open_connection(path)


def close_store() -> None:
    """Close cached connections. Tests call this between cases."""
    with _lock:
        for conn in _connections.values():
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            conn.close()
        _connections.clear()


def clean_label(label: str) -> str:
    if not isinstance(label, str):
        raise ValueError("label must be 1-80 characters without control characters")
    text = label.strip()
    if not text or len(text) > 80 or _LABEL_BAD.search(text):
        raise ValueError("label must be 1-80 characters without control characters")
    return text


def _base_url(override: str | None) -> str:
    raw = override if override is not None else os.environ.get("AVATAR_INVITE_BASE_URL", DEFAULT_BASE_URL)
    text = (raw or "").strip().rstrip("/")
    if not text or any(ch in text for ch in "\r\n\\ "):
        raise ValueError("base url rejected")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("base url rejected")
    if parts.username is not None or parts.password is not None:
        raise ValueError("base url rejected")
    if parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise ValueError("base url rejected")
    if not parts.hostname:
        raise ValueError("base url rejected")
    return text


def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _utc_day(now: float) -> str:
    """Calendar day in UTC, ``YYYY-MM-DD``. The daily bill counter uses this."""
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def effective_bills_today(invite: Invite, now: float | None = None) -> int:
    """Bills reserved on the UTC day that contains ``now``.

    A stored count from an earlier UTC day counts as zero. The next analyze
    persists that reset in the same transaction that reserves the bill.
    """
    moment = time.time() if now is None else now
    if invite.bills_day != _utc_day(moment):
        return 0
    return invite.bills_used_today


def _from_row(row: Mapping[str, Any]) -> Invite:
    return Invite(
        id=str(row["id"]),
        label=str(row["label"]),
        created_at=float(row["created_at"]),
        expires_at=float(row["expires_at"]),
        revoked=bool(int(row["revoked"])),
        bill_budget=int(row["bill_budget"]),
        bill_daily_budget=int(row["bill_daily_budget"]),
        llm_budget=int(row["llm_budget"]),
        bills_used=int(row["bills_used"]),
        bills_used_today=int(row["bills_used_today"]),
        bills_day=str(row["bills_day"] or ""),
        llm_calls_used=int(row["llm_calls_used"]),
    )


_PUBLIC_COLUMNS = (
    "id, label, created_at, expires_at, revoked, bill_budget, bill_daily_budget, "
    "llm_budget, bills_used, bills_used_today, bills_day, llm_calls_used"
)


def _check_budget(name: str, value: int | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("budgets must be zero or positive")
    return value


def _check_ttl(ttl_seconds: int | None) -> int:
    if ttl_seconds is None:
        ttl_seconds = default_ttl_seconds()
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("ttl must be positive")
    if ttl_seconds <= 0 or ttl_seconds > _MAX_TTL_SECONDS:
        raise ValueError("ttl must be positive")
    return ttl_seconds


def create_invite(
    *,
    label: str,
    bills: int | None = None,
    bills_daily: int | None = None,
    calls: int | None = None,
    ttl_seconds: int | None = None,
    base_url: str | None = None,
    token: str | None = None,
) -> IssuedInvite:
    """Create an invite and return the raw token once.

    ``token`` is for tests. The CLI does not pass it. Production callers leave
    it empty so the value comes from ``secrets.token_urlsafe(32)``.
    """
    label = clean_label(label)
    bill_budget = _check_budget("bills", bills, default_bill_budget())
    bill_daily_budget = _check_budget("bills_daily", bills_daily, default_bill_daily())
    llm_budget = _check_budget("calls", calls, default_llm_budget())
    ttl = _check_ttl(ttl_seconds)
    if token is None:
        token = secrets.token_urlsafe(TOKEN_BYTES)
    if not isinstance(token, str) or not _ISSUED_TOKEN_RE.fullmatch(token):
        raise ValueError("token rejected")
    link_base = _base_url(base_url)
    now = time.time()
    # Touch the key before hashing so the digest matches later lookups.
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = _ensure_key(data_dir)
    digest = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    invite_id = secrets.token_hex(8)
    expires_at = now + ttl
    with _lock:
        conn = _connect_write()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO invites (
                    id, label, token_hash, created_at, expires_at, revoked,
                    bill_budget, bill_daily_budget, llm_budget, bills_used,
                    bills_used_today, bills_day, llm_calls_used
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 0, 0, '', 0)
                """,
                (
                    invite_id, label, digest, now, expires_at,
                    bill_budget, bill_daily_budget, llm_budget,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    invite = Invite(
        id=invite_id,
        label=label,
        created_at=now,
        expires_at=expires_at,
        revoked=False,
        bill_budget=bill_budget,
        bill_daily_budget=bill_daily_budget,
        llm_budget=llm_budget,
        bills_used=0,
        bills_used_today=0,
        bills_day="",
        llm_calls_used=0,
    )
    return IssuedInvite(invite=invite, token=token, link=f"{link_base}/?token={quote(token, safe='')}")


def _presented_digest(token: str) -> str | None:
    if not isinstance(token, str) or not _PRESENTED_TOKEN_RE.fullmatch(token):
        return None
    key = _load_key_bytes()
    if key is None:
        hmac.new(_UNKEYED, token.encode("utf-8"), hashlib.sha256).digest()
        return None
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _select_by_hash(digest: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect_readonly()
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_PUBLIC_COLUMNS}, token_hash FROM invites WHERE token_hash = ?",
            (digest,),
        ).fetchone()
        return dict(row) if row else None


def authenticate(token: str) -> Invite | None:
    """Return the invite when the token is known, unexpired, and not revoked.

    Unknown, expired, and revoked tokens all return None. The digest comparison
    always runs, including when the token is not in the store.
    """
    try:
        digest = _presented_digest(token)
        row = _select_by_hash(digest) if digest else None
    except InvitePathError:
        digest = None
        row = None
    stored = str(row["token_hash"]) if row and row.get("token_hash") else _DUMMY_HASH
    left = digest if isinstance(digest, str) and len(digest) == len(stored) else _DUMMY_HASH
    try:
        matched = hmac.compare_digest(left, stored)
    except (TypeError, ValueError):
        return None
    if not matched or row is None:
        return None
    if int(row["revoked"]) or float(row["expires_at"]) <= time.time():
        return None
    return _from_row(row)


def get_invite(invite_id: str) -> Invite | None:
    if not isinstance(invite_id, str) or not _ID_RE.fullmatch(invite_id):
        return None
    with _lock:
        conn = _connect_readonly()
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM invites WHERE id = ?",
            (invite_id,),
        ).fetchone()
    if row is None:
        return None
    return _from_row(row)


def list_invites() -> list[Invite]:
    with _lock:
        conn = _connect_readonly()
        if conn is None:
            return []
        rows = conn.execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM invites ORDER BY created_at ASC, id ASC"
        ).fetchall()
    return [_from_row(row) for row in rows]


def revoke_invite(invite_id: str) -> bool:
    """Mark an invite revoked. True when that id exists, including if it already was."""
    if not isinstance(invite_id, str) or not _ID_RE.fullmatch(invite_id):
        return False
    with _lock:
        conn = _connect_readonly()
        if conn is None:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id FROM invites WHERE id = ?",
                (invite_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "UPDATE invites SET revoked = 1 WHERE id = ?",
                (invite_id,),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def expire_invite(invite_id: str) -> bool:
    """Set expiry in the past. Does not set the revoked flag."""
    if not isinstance(invite_id, str) or not _ID_RE.fullmatch(invite_id):
        return False
    now = time.time()
    with _lock:
        conn = _connect_readonly()
        if conn is None:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE invites SET expires_at = MIN(expires_at, ?) WHERE id = ?",
                (now - 1.0, invite_id),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return False
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def extend_invite(invite_id: str, *, hours: float) -> bool:
    """Push expiry forward from the later of now and the current expiry.

    A revoked invite stays revoked.
    """
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        raise ValueError("hours must be positive")
    if not math.isfinite(float(hours)) or float(hours) <= 0 or float(hours) > _MAX_EXTEND_HOURS:
        raise ValueError("hours must be positive")
    if not isinstance(invite_id, str) or not _ID_RE.fullmatch(invite_id):
        return False
    now = time.time()
    extra = float(hours) * 3600.0
    with _lock:
        conn = _connect_readonly()
        if conn is None:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT expires_at FROM invites WHERE id = ?",
                (invite_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False
            base = max(now, float(row["expires_at"]))
            conn.execute(
                "UPDATE invites SET expires_at = ? WHERE id = ?",
                (base + extra, invite_id),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _used_today(row: sqlite3.Row, today: str) -> int:
    if str(row["bills_day"] or "") != today:
        return 0
    return int(row["bills_used_today"])


def _classify(conn: sqlite3.Connection, invite_id: str, bills: int, calls: int, now: float) -> str:
    row = conn.execute(
        """
        SELECT revoked, expires_at, bills_used, bill_budget, bill_daily_budget,
               bills_used_today, bills_day, llm_calls_used, llm_budget
        FROM invites WHERE id = ?
        """,
        (invite_id,),
    ).fetchone()
    if row is None or int(row["revoked"]) or float(row["expires_at"]) <= now:
        return "closed"
    if bills and int(row["bills_used"]) + bills > int(row["bill_budget"]):
        return "bills"
    if bills and _used_today(row, _utc_day(now)) + bills > int(row["bill_daily_budget"]):
        return "daily"
    if calls and int(row["llm_calls_used"]) + calls > int(row["llm_budget"]):
        return "calls"
    return "calls"


def try_reserve(invite_id: str, bills: int, calls: int) -> str:
    """Atomically add ``bills`` and ``calls`` or change nothing.

    Returns ``ok``, ``bills`` (lifetime cap), ``daily`` (UTC-day cap),
    ``calls``, or ``closed`` (missing, expired, or revoked). A failed reserve
    does not increment any counter. The daily count resets when the UTC date
    changes, inside this same transaction.
    """
    if (
        isinstance(bills, bool)
        or isinstance(calls, bool)
        or not isinstance(bills, int)
        or not isinstance(calls, int)
        or bills < 0
        or calls < 0
    ):
        return "closed"
    if not isinstance(invite_id, str) or not _ID_RE.fullmatch(invite_id):
        return "closed"
    now = time.time()
    today = _utc_day(now)
    with _lock:
        conn = _connect_write()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT revoked, expires_at, bills_used, bill_budget, bill_daily_budget,
                       bills_used_today, bills_day, llm_calls_used, llm_budget
                FROM invites WHERE id = ?
                """,
                (invite_id,),
            ).fetchone()
            if row is None or int(row["revoked"]) or float(row["expires_at"]) <= now:
                conn.execute("ROLLBACK")
                return "closed"
            if int(row["bills_used"]) + bills > int(row["bill_budget"]):
                conn.execute("ROLLBACK")
                return "bills"
            used_today = _used_today(row, today)
            if bills and used_today + bills > int(row["bill_daily_budget"]):
                conn.execute("ROLLBACK")
                return "daily"
            if int(row["llm_calls_used"]) + calls > int(row["llm_budget"]):
                conn.execute("ROLLBACK")
                return "calls"
            if bills:
                cur = conn.execute(
                    """
                    UPDATE invites
                       SET bills_used = bills_used + ?,
                           llm_calls_used = llm_calls_used + ?,
                           bills_day = ?,
                           bills_used_today = CASE
                               WHEN bills_day = ? THEN bills_used_today + ?
                               ELSE ?
                           END
                     WHERE id = ?
                       AND revoked = 0
                       AND expires_at > ?
                       AND bills_used + ? <= bill_budget
                       AND llm_calls_used + ? <= llm_budget
                       AND (CASE WHEN bills_day = ? THEN bills_used_today ELSE 0 END) + ? <= bill_daily_budget
                    """,
                    (
                        bills, calls, today, today, bills, used_today + bills,
                        invite_id, now, bills, calls, today, bills,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE invites
                       SET llm_calls_used = llm_calls_used + ?
                     WHERE id = ?
                       AND revoked = 0
                       AND expires_at > ?
                       AND bills_used <= bill_budget
                       AND llm_calls_used + ? <= llm_budget
                    """,
                    (calls, invite_id, now, calls),
                )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return _classify(conn, invite_id, bills, calls, now)
            conn.execute("COMMIT")
            return "ok"
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise


def _cookie_values(request: Any, name: str) -> list[str]:
    found: list[str] = []
    headers = request.scope.get("headers") or ()
    for key, value in headers:
        if not isinstance(key, (bytes, bytearray)) or bytes(key).lower() != b"cookie":
            continue
        text = bytes(value).decode("latin-1") if isinstance(value, (bytes, bytearray)) else str(value)
        for part in text.split(";"):
            piece = part.strip()
            if "=" not in piece:
                continue
            cookie_name, cookie_value = piece.split("=", 1)
            if cookie_name.strip() == name:
                found.append(cookie_value.strip())
    return found


def authenticate_cookie(request: Any) -> Invite | None:
    """Accept exactly one invite cookie. Zero or several do not match."""
    values = _cookie_values(request, COOKIE_NAME)
    if len(values) != 1:
        return None
    return authenticate(values[0])


def _static_path(path: str) -> bool:
    if path in {"/manifest.webmanifest", "/sw.js"}:
        return True
    if path != "/static" and not path.startswith("/static/"):
        return False
    if ".." in path or "\\" in path or "\x00" in path:
        return False
    return True


def request_is_exempt(method: str, path: str) -> bool:
    """Static shell and the token landing do not require a cookie."""
    if _static_path(path):
        return True
    if method in {"GET", "HEAD"} and path in {"/", "/invite"}:
        return True
    return False


def denied_page():
    """Same HTML for a missing, unknown, expired, or revoked invite."""
    from starlette.responses import HTMLResponse

    return HTMLResponse(
        DENIED_HTML,
        status_code=403,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _location_without_token(request: Any, *, path: str | None = None) -> str:
    target = path if path is not None else request.url.path
    if (
        not isinstance(target, str)
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or "\n" in target
        or "\r" in target
    ):
        target = "/"
    pairs = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "token"
    ]
    query = urlencode(pairs, doseq=True)
    if query:
        return target + "?" + query
    return target


def _set_invite_cookie(response: Any, request: Any, token: str, invite: Invite) -> None:
    remaining = int(invite.expires_at - time.time())
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max(1, remaining),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )


def landing(request: Any, *, serve_index, redirect_path: str | None = None):
    """Swap ``?token=`` for a cookie, or serve the app when the cookie is valid.

    ``redirect_path`` is set for ``/invite``, which always sends a successful
    visit to ``/``. A bad token is the closed page, including when a cookie
    was already present.
    """
    from starlette.responses import RedirectResponse

    values = list(request.query_params.getlist("token"))
    if values:
        record = authenticate(values[0]) if len(values) == 1 else None
        if record is None:
            return denied_page()
        request.state.invite = record
        target = redirect_path if redirect_path is not None else _location_without_token(request)
        response = RedirectResponse(
            target,
            status_code=303,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
        _set_invite_cookie(response, request, values[0], record)
        return response
    if redirect_path is not None:
        record = authenticate_cookie(request)
        if record is None:
            return denied_page()
        request.state.invite = record
        return RedirectResponse(redirect_path, status_code=303)
    record = authenticate_cookie(request)
    if record is None:
        return denied_page()
    request.state.invite = record
    return serve_index()


def _usage_from_response(response: Any) -> tuple[int, int | None] | None:
    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or not body or body[:1] not in {b"{", b"["}:
        return None
    if len(body) > 2_000_000:
        return None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    found: list[dict[str, Any]] = []

    def walk(value: Any, depth: int) -> None:
        if depth > 6 or len(found) > 20:
            return
        if isinstance(value, dict):
            usage = value.get("token_usage")
            if isinstance(usage, list):
                for item in usage:
                    if isinstance(item, dict):
                        found.append(item)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:50]:
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)

    walk(payload, 0)
    if not found:
        return None
    calls = 0
    tokens = 0
    known = False
    for item in found:
        count = item.get("calls")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            calls += count
        for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
            number = item.get(key)
            if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
                tokens += number
                known = True
    return calls, (tokens if known else None)


def _append_usage(entry: dict[str, Any]) -> None:
    try:
        data_dir = resolve_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        path = _child(data_dir, LOG_NAME)
    except InvitePathError:
        return
    line = json.dumps(entry, separators=(",", ":"), sort_keys=True)
    lowered = line.lower()
    if "\n" in line or "\r" in line or "?token=" in lowered or "avatar_invite=" in lowered:
        return
    with _lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        _tighten(path)


def record_request(request: Any, status: int, response: Any, usage: tuple[int, int | None] | None = None) -> None:
    """Append one usage line for an authenticated invite request.

    The line has the invite id and label, never the raw token or the query string.
    """
    if not invite_mode_enabled():
        return
    state = getattr(request, "state", None)
    invite = getattr(state, "invite", None)
    if not isinstance(invite, Invite):
        return
    if not _ID_RE.fullmatch(invite.id):
        return
    calls, tokens = usage if usage is not None else (0, None)
    observed = _usage_from_response(response)
    if observed is not None:
        body_calls, body_tokens = observed
        if body_calls > calls:
            calls = body_calls
        if body_tokens is not None and (tokens is None or body_tokens > tokens):
            tokens = body_tokens
    path = request.url.path if isinstance(request.url.path, str) else "/"
    if len(path) > 200 or any(ch in path for ch in "\r\n\x00"):
        path = "/"
    method = request.method if isinstance(request.method, str) else "GET"
    if not method.isalpha() or len(method) > 10:
        method = "GET"
    if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int)):
        tokens = None
    entry = {
        "invite_id": invite.id,
        "label": invite.label,
        "llm_calls": int(calls) if not isinstance(calls, bool) else 0,
        "llm_tokens": tokens,
        "route": f"{method} {path}",
        "status": int(status),
        "timestamp": _iso(time.time()),
    }
    try:
        _append_usage(entry)
    except (OSError, InvitePathError, sqlite3.Error, TypeError, ValueError):
        print("invite usage log write failed", file=sys.stderr)


def read_usage(invite_id: str | None = None) -> list[dict[str, Any]]:
    if invite_id is not None and not _ID_RE.fullmatch(invite_id):
        return []
    try:
        path = _child(resolve_data_dir(), LOG_NAME)
    except InvitePathError:
        return []
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if invite_id is not None and item.get("invite_id") != invite_id:
                continue
            events.append({key: value for key, value in item.items() if key not in _SECRET_LOG_KEYS})
    return events


def _redact_text(value: str) -> str:
    value = _TOKEN_IN_TEXT.sub(r"\1[redacted]", value)
    value = _COOKIE_IN_TEXT.sub(r"\1[redacted]", value)
    return value


class _RedactInviteTokenFilter(logging.Filter):
    """Drop invite tokens from log lines. The raw token must not land in access logs.

    Redaction runs on the formatted message. A token passed as its own ``%s``
    argument is removed too, which is how an access log prints the query string
    and how a cookie header would be printed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            return True
        redacted = _redact_text(rendered)
        record.msg = redacted
        record.args = ()
        return True


def install_log_redaction() -> None:
    """Attach the redaction filter to the root logger and uvicorn's loggers."""
    names = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "uvicorn.asgi")
    for name in names:
        logger = logging.getLogger(name)
        if any(isinstance(item, _RedactInviteTokenFilter) for item in logger.filters):
            continue
        logger.addFilter(_RedactInviteTokenFilter())


def _print_issued(issued: IssuedInvite) -> None:
    print("Shown once. It is not stored.")
    print(issued.link)
    print(
        f"id={issued.invite.id} label={issued.invite.label} "
        f"expires={_iso(issued.invite.expires_at)} "
        f"bills={issued.invite.bill_budget} daily={issued.invite.bill_daily_budget} "
        f"calls={issued.invite.llm_budget}"
    )


def format_list(rows: list[Invite]) -> str:
    lines = ["id\tlabel\tcreated\texpires\trevoked\tbills\tdaily\tcalls"]
    now = time.time()
    for row in rows:
        lines.append(
            "\t".join(
                [
                    row.id,
                    row.label.replace("\t", " "),
                    _iso(row.created_at),
                    _iso(row.expires_at),
                    "yes" if row.revoked else "no",
                    f"{row.bills_used}/{row.bill_budget}",
                    f"{effective_bills_today(row, now)}/{row.bill_daily_budget}",
                    f"{row.llm_calls_used}/{row.llm_budget}",
                ]
            )
        )
    return "\n".join(lines)


def format_usage(rows: list[Invite], events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    now = time.time()
    for row in rows:
        lines.append(
            f"{row.id}\t{row.label}\tbills {row.bills_used}/{row.bill_budget}\t"
            f"daily {effective_bills_today(row, now)}/{row.bill_daily_budget}\t"
            f"calls {row.llm_calls_used}/{row.llm_budget}\t"
            f"revoked={'yes' if row.revoked else 'no'}"
        )
    if not events:
        lines.append("(no requests)")
    for event in events:
        tokens = event.get("llm_tokens")
        shown = "-" if tokens is None else str(tokens)
        lines.append(
            f"{event.get('timestamp')}\t{event.get('route')}\tstatus {event.get('status')}\t"
            f"llm_calls {event.get('llm_calls')}\tllm_tokens {shown}\t{event.get('invite_id')}"
        )
    return "\n".join(lines)


def _require_id(invite_id: str) -> Invite:
    invite = get_invite(invite_id)
    if invite is None:
        raise ValueError("not found")
    return invite


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Does not bind a port and does not import the ASGI app."""
    parser = argparse.ArgumentParser(
        prog="python -m backend.invites",
        description="Create and revoke invite tokens for a closed demo. Not an HTTP service.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an invite and print its link once")
    create.add_argument("--label", required=True)
    create.add_argument("--bills", type=int, default=None)
    create.add_argument("--bills-daily", type=int, default=None)
    create.add_argument("--calls", type=int, default=None)
    create.add_argument("--ttl-hours", type=float, default=None)
    create.add_argument("--base-url", default=None)

    sub.add_parser("list", help="List invites without token values")

    revoke = sub.add_parser("revoke", help="Revoke an invite by id")
    revoke.add_argument("invite_id")

    extend = sub.add_parser("extend", help="Extend an invite from now or its current expiry")
    extend.add_argument("invite_id")
    extend.add_argument("--hours", type=float, required=True)

    expire = sub.add_parser("expire", help="Expire an invite immediately")
    expire.add_argument("invite_id")

    usage = sub.add_parser("usage", help="Show counters and the append-only request log")
    usage.add_argument("invite_id", nargs="?")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            ttl = None
            if args.ttl_hours is not None:
                if (
                    isinstance(args.ttl_hours, bool)
                    or not math.isfinite(args.ttl_hours)
                    or args.ttl_hours <= 0
                    or args.ttl_hours > _MAX_EXTEND_HOURS
                ):
                    raise ValueError("ttl must be positive")
                ttl = int(args.ttl_hours * 3600)
                if ttl <= 0:
                    raise ValueError("ttl must be positive")
            issued = create_invite(
                label=args.label,
                bills=args.bills,
                bills_daily=args.bills_daily,
                calls=args.calls,
                ttl_seconds=ttl,
                base_url=args.base_url,
            )
            _print_issued(issued)
            return 0
        if args.command == "list":
            print(format_list(list_invites()))
            return 0
        if args.command == "revoke":
            if not revoke_invite(args.invite_id):
                raise ValueError("not found")
            print(f"revoked {args.invite_id}")
            return 0
        if args.command == "extend":
            if not extend_invite(args.invite_id, hours=args.hours):
                raise ValueError("not found")
            invite = _require_id(args.invite_id)
            print(f"extended {invite.id} expires={_iso(invite.expires_at)}")
            return 0
        if args.command == "expire":
            if not expire_invite(args.invite_id):
                raise ValueError("not found")
            print(f"expired {args.invite_id}")
            return 0
        if args.command == "usage":
            rows = list_invites()
            if args.invite_id:
                invite = get_invite(args.invite_id)
                if invite is None:
                    raise ValueError("not found")
                rows = [invite]
            print(format_usage(rows, read_usage(args.invite_id)))
            return 0
    except InvitePathError:
        print("invite data dir rejected", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("not found", file=sys.stderr)
    return 1


install_log_redaction()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1) from None
