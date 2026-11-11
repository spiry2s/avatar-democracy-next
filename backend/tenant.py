"""
Which citizen the current request acts as.

Bills are deliberately shared: every invited tester sees the same drafts and
the same chamber, like a real legislature. What must not be shared is the
identity behind an endorsement. In invite mode the middleware pushes the
invite id for each authenticated request, and endorsements, revocations, and
bill sponsorship are recorded under that id. Outside invite mode the single
local citizen is ``local``.
"""

from __future__ import annotations

import contextvars
import re

LOCAL_CITIZEN = "local"
_SCOPE_ID = re.compile(r"[0-9a-f]{16}\Z")

_citizen: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "avatar_tenant_citizen", default=None
)


def push_scope(invite_id: str) -> contextvars.Token:
    if not _SCOPE_ID.fullmatch(invite_id):
        raise ValueError("bad tenant scope")
    return _citizen.set(invite_id)


def reset_scope(token: contextvars.Token) -> None:
    _citizen.reset(token)


def citizen_id() -> str:
    """The id an endorsement or a sponsorship is recorded under."""
    return _citizen.get() or LOCAL_CITIZEN
