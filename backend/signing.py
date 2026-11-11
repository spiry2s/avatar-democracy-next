"""
Server signatures on bill analyses (issue #7).

``POST /api/avatar/vote`` receives the analysis from the browser. Its quote
checks (``grounded``) and its model-comparison report are what keep the
Avatar from voting on unverified claims, so they must not be taken on the
client's word. ``/api/analyze`` signs every analysis it returns; the vote
route checks the signature and treats anything unsigned or altered as
unverified.

The signature is an HMAC-SHA256 over a canonical JSON form of the analysis,
stored inside the analysis as ``server_signature``. The browser passes the
object through unchanged, so no frontend change is needed. The canonical form
writes a float with no fractional part as an integer, because a JavaScript
round trip turns ``1.0`` into ``1``.

The key is ``AVATAR_SIGNING_KEY`` (at least 32 characters) when set, so
several server processes can share it. Otherwise 32 random bytes are kept in
``analysis-signing.key`` in the invite data directory, which is the persisted
volume on a hosted deployment, so signatures survive a restart.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import threading
from typing import Any

from backend import invites

SIGNATURE_FIELD = "server_signature"
KEY_FILE = "analysis-signing.key"
_DOMAIN = b"avatar-democracy/analysis/v1\n"
_MIN_ENV_KEY_CHARS = 32

_key_lock = threading.Lock()
_cached_key: tuple[str, bytes] | None = None


class SigningUnavailable(RuntimeError):
    """No usable signing key could be loaded or created."""


def _load_key() -> bytes:
    """The signing key. Cached per source so tests can switch directories."""
    global _cached_key
    env = (os.environ.get("AVATAR_SIGNING_KEY") or "").strip()
    if env:
        if len(env) < _MIN_ENV_KEY_CHARS:
            raise SigningUnavailable("AVATAR_SIGNING_KEY must be at least 32 characters")
        return hashlib.sha256(env.encode("utf-8")).digest()

    try:
        data_dir = invites.resolve_data_dir()
    except invites.InvitePathError as exc:
        raise SigningUnavailable("signing key directory rejected") from exc
    path = data_dir / KEY_FILE
    source = str(path)
    with _key_lock:
        if _cached_key is not None and _cached_key[0] == source:
            return _cached_key[1]
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            # O_BINARY: on Windows a text-mode fd would expand 0x0A bytes.
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            key = path.read_bytes()
        else:
            key = secrets.token_bytes(32)
            try:
                os.write(fd, key)
            finally:
                os.close(fd)
        if len(key) != 32:
            raise SigningUnavailable(f"{KEY_FILE} is not a 32-byte key")
        _cached_key = (source, key)
        return key


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("analysis contains a non-finite number")
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {str(k): _canonical_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(v) for v in value]
    raise ValueError(f"analysis contains an unsupported value: {type(value).__name__}")


def _canonical_bytes(analysis: dict[str, Any]) -> bytes:
    body = {k: v for k, v in analysis.items() if k != SIGNATURE_FIELD}
    text = json.dumps(
        _canonical_value(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return text.encode("utf-8")


def signature_for(analysis: dict[str, Any]) -> str:
    return hmac.new(_load_key(), _DOMAIN + _canonical_bytes(analysis), hashlib.sha256).hexdigest()


def sign(analysis: dict[str, Any]) -> dict[str, Any]:
    """Attach ``server_signature`` in place and return the analysis."""
    analysis[SIGNATURE_FIELD] = signature_for(analysis)
    return analysis


def verify(analysis: Any) -> bool:
    """True only for an analysis this server signed and nobody changed since."""
    if not isinstance(analysis, dict):
        return False
    presented = analysis.get(SIGNATURE_FIELD)
    if not isinstance(presented, str) or len(presented) != 64:
        return False
    try:
        expected = signature_for(analysis)
    except (ValueError, SigningUnavailable):
        return False
    return hmac.compare_digest(presented, expected)
