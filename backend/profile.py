"""
The citizen profile: the configuration an Avatar votes from.

Hybrid configuration model:
  1. compass         — seeded by the questionnaire (questionnaire.py)
  2. values          — free-text statement the citizen writes
  3. delegates       — per-issue trusted sources ("on climate, follow Dr. Y")

Asymmetric edit rate-limits (white paper, decision 4):
  - Adding/replacing a delegate enters a COOLING-OFF period before it becomes
    active. The system is deliberately slow to grant new delegated authority.
  - Revoking a delegate is IMMEDIATE. The system is fast to withdraw trust.

Persistence is a single JSON file. This is a local, single-citizen build; a real
deployment would key profiles by verified eID. citizen_id defaults to "local".
"""

from __future__ import annotations

import contextvars

import os
import re
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from backend import invites, storage

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILE_FILE = DATA_DIR / "profile.json"
_SCOPE_ID = re.compile(r"[0-9a-f]{16}\Z")
_scope: contextvars.ContextVar[str | None] = contextvars.ContextVar("avatar_profile_scope", default=None)
_root: contextvars.ContextVar[Path | None] = contextvars.ContextVar("avatar_profile_root", default=None)

# How long a newly-added delegate sits in cooling-off before the Avatar will
# follow it. White paper suggests "one week or more". Override with the env var
# DELEGATE_COOLING_OFF_SECONDS (set to 0 for testing/demos).
DEFAULT_COOLING_OFF_SECONDS = 7 * 24 * 3600


def cooling_off_seconds() -> int:
    raw = os.environ.get("DELEGATE_COOLING_OFF_SECONDS")
    if raw is None:
        return DEFAULT_COOLING_OFF_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_COOLING_OFF_SECONDS


@dataclass
class Delegate:
    """A per-issue delegated source."""

    issue_area: str          # e.g. "fiscal", "climate", "social"
    name: str                # e.g. "Sen. Rand Paul", "Dr. James Hansen"
    note: str = ""           # optional citizen note on why / scope
    added_at: float = field(default_factory=time.time)
    active_at: float = 0.0   # timestamp when this delegate becomes active
    sources: list[dict[str, Any]] = field(default_factory=list)  # grounded positions

    def is_active(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return now >= self.active_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["active"] = self.is_active()
        d["pending_seconds_remaining"] = max(0, int(self.active_at - time.time()))
        return d


@dataclass
class CitizenProfile:
    citizen_id: str = "local"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    compass: dict[str, float] = field(default_factory=dict)
    questionnaire_answers: dict[str, int] = field(default_factory=dict)
    values: str = ""
    delegates: list[Delegate] = field(default_factory=list)

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "citizen_id": self.citizen_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "compass": self.compass,
            "questionnaire_answers": self.questionnaire_answers,
            "values": self.values,
            "delegates": [d.to_dict() for d in self.delegates],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CitizenProfile":
        delegates = []
        for d in data.get("delegates", []):
            delegates.append(Delegate(
                issue_area=d["issue_area"],
                name=d["name"],
                note=d.get("note", ""),
                added_at=d.get("added_at", time.time()),
                active_at=d.get("active_at", 0.0),
                sources=d.get("sources", []),
            ))
        return cls(
            citizen_id=data.get("citizen_id", "local"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            compass=data.get("compass", {}),
            questionnaire_answers=data.get("questionnaire_answers", {}),
            values=data.get("values", ""),
            delegates=delegates,
        )

    # ── delegate edits (asymmetric) ───────────────────────────────────────────

    def active_delegates(self, now: float | None = None) -> list[Delegate]:
        return [d for d in self.delegates if d.is_active(now)]

    def add_delegate(self, issue_area: str, name: str, note: str = "") -> Delegate:
        """
        Add or replace the delegate for an issue area. Enters cooling-off:
        will not be followed until active_at. Slow to grant authority.
        """
        issue_area = issue_area.strip().lower()
        name = name.strip()
        if not issue_area or not name:
            raise ValueError("issue_area and name are required")

        now = time.time()
        delegate = Delegate(
            issue_area=issue_area,
            name=name,
            note=note.strip(),
            added_at=now,
            active_at=now + cooling_off_seconds(),
        )
        # One delegate per issue area: newest replaces any prior (active or pending).
        self.delegates = [d for d in self.delegates if d.issue_area != issue_area]
        self.delegates.append(delegate)
        self.updated_at = now
        return delegate

    def revoke_delegate(self, issue_area: str) -> bool:
        """Remove the delegate for an issue area immediately. Fast to withdraw trust."""
        issue_area = issue_area.strip().lower()
        before = len(self.delegates)
        self.delegates = [d for d in self.delegates if d.issue_area != issue_area]
        removed = len(self.delegates) < before
        if removed:
            self.updated_at = time.time()
        return removed

    # ── delegate grounding (recorded positions) ───────────────────────────────

    def add_source(self, issue_area: str, title: str, text: str) -> dict[str, Any]:
        """Attach a recorded position to a delegate so the Avatar can cite evidence.
        No cooling-off: this clarifies an existing delegation, it doesn't grant new authority."""
        issue_area = issue_area.strip().lower()
        text = text.strip()
        if not text:
            raise ValueError("Source text is required")
        for d in self.delegates:
            if d.issue_area == issue_area:
                src = {
                    "id": uuid.uuid4().hex[:8],
                    "title": title.strip() or "position",
                    "text": text,
                    "added_at": time.time(),
                }
                d.sources.append(src)
                self.updated_at = time.time()
                return src
        raise ValueError(f"No delegate for issue area '{issue_area}'")

    def remove_source(self, issue_area: str, source_id: str) -> bool:
        issue_area = issue_area.strip().lower()
        for d in self.delegates:
            if d.issue_area == issue_area:
                before = len(d.sources)
                d.sources = [s for s in d.sources if s.get("id") != source_id]
                if len(d.sources) < before:
                    self.updated_at = time.time()
                    return True
        return False


# ── persistence ────────────────────────────────────────────────────────────────

def push_scope(scope: str, root: Path) -> tuple[contextvars.Token, contextvars.Token]:
    """Remember which invite's profile this request may read or write.

    ``scope`` is the invite id, not the raw invite. ``root`` is that invite
    store's directory. The shared ``profile.json`` is not used while this is set.
    """
    if not _SCOPE_ID.fullmatch(scope):
        raise ValueError("bad profile scope")
    return (_scope.set(scope), _root.set(root))


def reset_scope(token: tuple[contextvars.Token, contextvars.Token]) -> None:
    scope_token, root_token = token
    _scope.reset(scope_token)
    _root.reset(root_token)


def _profile_path() -> Path | None:
    """File for this request.

    With invite mode on and no scope, return None so a handler cannot fall
    through to the shared local profile. With invite mode off, use that file.
    """
    scope = _scope.get()
    if scope is None:
        if invites.invite_mode_enabled():
            return None
        return PROFILE_FILE
    root = _root.get()
    if root is None or not _SCOPE_ID.fullmatch(scope):
        return None
    directory = (root / "profiles").resolve()
    path = (directory / f"{scope}.json").resolve()
    if path.parent != directory:
        return None
    return path


def _tighten(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_profile() -> CitizenProfile | None:
    """Load the stored profile, or None if none exists yet.

    A file that exists but cannot be read raises storage.CorruptStateError.
    It must not read as "no profile": the next save would replace the
    citizen's values and delegates with an empty profile.
    """
    path = _profile_path()
    if path is None:
        return None
    data = storage.read_json(path)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise storage.CorruptStateError(path, ValueError("not a JSON object"))
    try:
        return CitizenProfile.from_dict(data)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise storage.CorruptStateError(path, exc) from exc


def save_profile(profile: CitizenProfile) -> None:
    """Persist the profile to disk."""
    path = _profile_path()
    if path is None:
        raise RuntimeError("profile scope missing")
    scope = _scope.get()
    if scope is not None:
        profile.citizen_id = scope
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    profile.updated_at = time.time()
    storage.write_json(path, profile.to_dict())
    _tighten(path)
