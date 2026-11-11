"""
Bill lifecycle: the process around a bill, not just its text.

A bill moves through: draft -> endorsing -> cooling_off -> voting -> passed|failed
(white paper decisions 6-17). State transitions here are PURE functions on Bill
objects (no I/O, no API calls) so they're cheap to unit-test. The analysis and
the Avatar vote — which do hit the API — are orchestrated in main.py and their
results cached back onto the bill.

The vote tally is modeled over COUNTS of voters per section, so today's single
operator Avatar (a chamber of size 1) and the later Proxy Chamber (a population)
share the same passage logic.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend import config, storage

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BILLS_DIR = DATA_DIR / "bills"

VALID_SCOPES = ("ordinary", "major", "constitutional")


class BillState:
    DRAFT = "draft"
    ENDORSING = "endorsing"
    COOLING_OFF = "cooling_off"
    VOTING = "voting"
    PASSED = "passed"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


@dataclass
class Bill:
    id: str
    title: str
    sponsor: str
    scope: str
    state: str
    created_at: float
    updated_at: float
    versions: list[dict[str, Any]]
    current_version: int
    endorsements: list[dict[str, Any]]
    simulated_endorsements: int
    endorsement_threshold: int
    cooling_off_until: float | None = None
    frozen_version: int | None = None
    analysis: dict[str, Any] | None = None
    vote: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    emergency: bool = False
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "title": self.title,
            "sponsor": self.sponsor,
            "scope": self.scope,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "versions": self.versions,
            "current_version": self.current_version,
            "endorsements": self.endorsements,
            "simulated_endorsements": self.simulated_endorsements,
            "endorsement_threshold": self.endorsement_threshold,
            "cooling_off_until": self.cooling_off_until,
            "frozen_version": self.frozen_version,
            "analysis": self.analysis,
            "vote": self.vote,
            "result": self.result,
            "emergency": self.emergency,
            "conflicts": self.conflicts,
        }
        # Derived, convenient for the UI:
        d["total_endorsements"] = total_endorsements(self)
        if self.cooling_off_until:
            d["cooling_off_remaining"] = max(0, int(self.cooling_off_until - time.time()))
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bill":
        return cls(
            id=data["id"],
            title=data["title"],
            sponsor=data.get("sponsor", "local"),
            scope=data.get("scope", "ordinary"),
            state=data.get("state", BillState.DRAFT),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            versions=data.get("versions", []),
            current_version=data.get("current_version", 1),
            endorsements=data.get("endorsements", []),
            simulated_endorsements=data.get("simulated_endorsements", 0),
            endorsement_threshold=data.get("endorsement_threshold", 0),
            cooling_off_until=data.get("cooling_off_until"),
            frozen_version=data.get("frozen_version"),
            analysis=data.get("analysis"),
            vote=data.get("vote"),
            result=data.get("result"),
            emergency=data.get("emergency", False),
            conflicts=data.get("conflicts", []),
        )


# ── pure transitions ─────────────────────────────────────────────────────────


def propose(title: str, text: str, scope: str = "ordinary", sponsor: str = "local",
            emergency: bool = False) -> Bill:
    if not text or not text.strip():
        raise ValueError("Bill text is required")
    scope = scope if scope in VALID_SCOPES else "ordinary"
    base = config.ENDORSEMENT_THRESHOLDS.get(scope, 25)
    # Emergency fast-track must clear a HIGHER bar to invoke.
    threshold = base * config.EMERGENCY_THRESHOLD_MULTIPLIER if emergency else base
    now = time.time()
    return Bill(
        id="bill-" + uuid.uuid4().hex[:8],
        title=(title or "Untitled bill").strip(),
        sponsor=sponsor,
        scope=scope,
        state=BillState.DRAFT,
        created_at=now,
        updated_at=now,
        versions=[{"version": 1, "text": text, "note": "initial draft", "created_at": now}],
        current_version=1,
        endorsements=[],
        simulated_endorsements=0,
        endorsement_threshold=int(threshold),
        emergency=emergency,
    )


def add_version(bill: Bill, text: str, note: str = "") -> None:
    if bill.state != BillState.DRAFT:
        raise ValueError("Versions can only be edited while the bill is a draft")
    if not text or not text.strip():
        raise ValueError("Version text is required")
    v = bill.current_version + 1
    bill.versions.append({"version": v, "text": text, "note": note, "created_at": time.time()})
    bill.current_version = v
    bill.updated_at = time.time()


def open_for_endorsement(bill: Bill) -> None:
    if bill.state != BillState.DRAFT:
        raise ValueError("Only a draft bill can be opened for endorsement")
    bill.state = BillState.ENDORSING
    bill.updated_at = time.time()


def total_endorsements(bill: Bill) -> int:
    return len(bill.endorsements) + bill.simulated_endorsements


def endorse(bill: Bill, citizen_id: str = "local") -> None:
    if bill.state != BillState.ENDORSING:
        raise ValueError("Bill is not open for endorsement")
    if any(e["citizen_id"] == citizen_id for e in bill.endorsements):
        return  # already endorsed; idempotent
    now = time.time()
    bill.endorsements.append({
        "citizen_id": citizen_id,
        "created_at": now,
        "revocable_until": now + config.ENDORSEMENT_REVOCATION_SECONDS,
    })
    bill.updated_at = now


def revoke_endorsement(bill: Bill, citizen_id: str = "local") -> bool:
    """Revoke within the revocation window. Returns False if not found or locked."""
    now = time.time()
    for e in bill.endorsements:
        if e["citizen_id"] == citizen_id:
            if now > e.get("revocable_until", 0):
                return False  # window passed; locked in
            bill.endorsements = [x for x in bill.endorsements if x["citizen_id"] != citizen_id]
            bill.updated_at = now
            return True
    return False


def freeze_if_threshold_met(bill: Bill) -> bool:
    """If endorsements meet the threshold, freeze the current version and start
    the cooling-off clock. Returns True if it just froze."""
    if bill.state != BillState.ENDORSING:
        return False
    if total_endorsements(bill) < bill.endorsement_threshold:
        return False
    bill.state = BillState.COOLING_OFF
    bill.frozen_version = bill.current_version
    cool = config.EMERGENCY_COOLING_OFF_SECONDS if bill.emergency else config.BILL_COOLING_OFF_SECONDS
    bill.cooling_off_until = time.time() + cool
    bill.updated_at = time.time()
    return True


def refresh_state(bill: Bill) -> bool:
    """Lazy clock: promote cooling_off -> voting once the timer elapses.
    Called on reads so no background scheduler is needed. Returns True if changed."""
    if (
        bill.state == BillState.COOLING_OFF
        and bill.cooling_off_until is not None
        and time.time() >= bill.cooling_off_until
    ):
        bill.state = BillState.VOTING
        bill.updated_at = time.time()
        return True
    return False


def frozen_text(bill: Bill) -> str:
    """The text of the version frozen for voting (falls back to current)."""
    target = bill.frozen_version if bill.frozen_version is not None else bill.current_version
    for v in bill.versions:
        if v["version"] == target:
            return v["text"]
    return bill.versions[-1]["text"] if bill.versions else ""


# ── tally / result (generalizes from 1 Avatar to a population) ───────────────


def tally_single_vote(avatar_vote: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one Avatar's per-section vote into per-section counts (chamber of 1)."""
    tallies = []
    for sv in avatar_vote.get("section_votes", []):
        pos = sv.get("position", "abstain")
        tallies.append({
            "section_id": sv.get("section_id", "?"),
            "heading": sv.get("heading", ""),
            "yes": 1 if pos == "yes" else 0,
            "no": 1 if pos == "no" else 0,
            "abstain": 1 if pos == "abstain" else 0,
        })
    return tallies


def compute_result(section_tallies: list[dict[str, Any]], ratio: float) -> dict[str, Any]:
    """A section passes if yes/(yes+no) >= ratio. A section with no decisive votes
    (everyone abstained — e.g. a short title or findings) is "no contest" and does
    NOT block the bill. The bill passes only if every CONTESTED section passes
    (per-section voting; no Christmas-tree bills).

    A section marked ``withheld`` (its quote could not be verified, so nobody
    was allowed to vote on it) is not "no contest": it blocks the bill. An
    unverified section must not become law without a vote.
    """
    sections = []
    passed_count = 0
    contested = 0
    withheld: list[str] = []
    for t in section_tallies:
        decisive = t["yes"] + t["no"]
        is_withheld = bool(t.get("withheld"))
        is_contested = decisive > 0 and not is_withheld
        passed = is_contested and (t["yes"] / decisive) >= ratio
        if is_contested:
            contested += 1
        if passed:
            passed_count += 1
        if is_withheld:
            withheld.append(str(t.get("section_id", "?")))
        sections.append({**t, "passed": passed, "contested": is_contested})
    result = {
        "per_section": sections,
        "sections_passed": passed_count,
        "sections_total": len(sections),
        "sections_contested": contested,
        "sections_withheld": withheld,
        "passed": not withheld and contested > 0 and passed_count == contested,
    }
    if withheld:
        result["blocked_reason"] = (
            "Not passed: section(s) "
            + ", ".join(withheld)
            + " could not be verified against the bill text, so nobody voted on them. "
            "An unverified section cannot become law. Re-analyze or amend the bill."
        )
    return result


# ── persistence ──────────────────────────────────────────────────────────────


_BILL_ID = re.compile(r"bill-[0-9a-f]{1,32}\Z")


def _bill_path(bill_id: str) -> Path | None:
    """File for a bill id, or None for an id this module never issues.

    Ids come from the URL. Only ``bill-<hex>`` maps to a file, so ``..`` or a
    Windows backslash cannot address anything outside BILLS_DIR.
    """
    if not isinstance(bill_id, str) or not _BILL_ID.fullmatch(bill_id):
        return None
    return BILLS_DIR / f"{bill_id}.json"


def save_bill(bill: Bill) -> None:
    path = _bill_path(bill.id)
    if path is None:
        raise ValueError(f"invalid bill id {bill.id!r}")
    bill.updated_at = time.time()
    # to_dict adds derived fields; strip them before persisting the canonical form
    data = bill.to_dict()
    for derived in ("total_endorsements", "cooling_off_remaining"):
        data.pop(derived, None)
    storage.write_json(path, data)


def _bill_from(path: Path, data: Any) -> Bill:
    if not isinstance(data, dict):
        raise storage.CorruptStateError(path, ValueError("not a JSON object"))
    try:
        return Bill.from_dict(data)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise storage.CorruptStateError(path, exc) from exc


def load_bill(bill_id: str) -> Bill | None:
    """The bill, or None when it does not exist.

    A bill file that exists but cannot be read raises storage.CorruptStateError
    instead of looking like "not found".
    """
    path = _bill_path(bill_id)
    if path is None:
        return None
    data = storage.read_json(path)
    if data is None:
        return None
    return _bill_from(path, data)


def list_bills() -> list[Bill]:
    """Every readable bill, newest first. An unreadable file is skipped and logged."""
    if not BILLS_DIR.exists():
        return []
    bills = []
    for path in BILLS_DIR.glob("bill-*.json"):
        try:
            data = storage.read_json(path)
            if data is not None:
                bills.append(_bill_from(path, data))
        except storage.CorruptStateError as exc:
            log.warning("skipping unreadable bill file %s: %s", path.name, exc)
    bills.sort(key=lambda b: b.created_at, reverse=True)
    return bills
