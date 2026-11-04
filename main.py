"""
FastAPI server for Avatar Democracy.

Run with:
    python main.py

Endpoints that call the model are plain `def` (not `async def`): FastAPI runs
them in a thread pool, so a minutes-long vote never blocks other requests.
Read-modify-write cycles take a storage lock so concurrent requests can't
interleave.

Access control:
- State-changing requests from another origin are refused (CSRF defense — any
  website could otherwise POST to http://127.0.0.1:8000 and spend API credits).
- If AVATAR_ACCESS_TOKEN is set, every request needs it (query ?token=, header
  X-Access-Token, or the cookie set after the first ?token= visit). Binding to a
  non-loopback address without a token generates one at startup.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal, Optional
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import anthropic  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from backend import (  # noqa: E402
    audit,
    avatar,
    bills as bills_mod,
    chamber,
    config,
    conflict,
    extractors,

    population,
    profile as profile_mod,
    questionnaire,
    storage,
    summarizer,
)

log = logging.getLogger("avatar_democracy")

PROJECT_ROOT = Path(__file__).parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
TOKEN_COOKIE = "ad_token"

app = FastAPI(
    title="Avatar Democracy",
    description="Plain-language bill analysis, per-citizen Avatar voting, and a Proxy Chamber.",
    version="0.2.0",
)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── access control ────────────────────────────────────────────────────────────


@app.middleware("http")
async def access_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)

    token = config.ACCESS_TOKEN
    if not token:
        return await call_next(request)

    from_query = request.query_params.get("token")
    supplied = from_query or request.headers.get("x-access-token") or request.cookies.get(TOKEN_COOKIE)
    if not supplied or not hmac.compare_digest(supplied, token):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Access token required."}, status_code=401)
        return PlainTextResponse(
            "Access token required. Open the URL printed by the server (it ends in ?token=...).",
            status_code=401,
        )

    response = await call_next(request)
    if from_query:
        response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict")
    return response


@app.exception_handler(storage.CorruptStateError)
async def corrupt_state(_request: Request, exc: storage.CorruptStateError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=500)


@contextmanager
def model_errors(what: str) -> Iterator[None]:
    """Map model-call failures to HTTP errors with a useful message."""
    try:
        yield
    except HTTPException:
        raise
    except storage.CorruptStateError:
        raise
    except RuntimeError as e:  # configuration (missing key)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"{what}: Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise HTTPException(status_code=502, detail=f"{what}: could not reach the Claude API") from e
    except ValueError as e:  # llm.LLMError and other unusable output
        raise HTTPException(status_code=502, detail=f"{what}: {e}") from e
    except Exception as e:
        log.exception("%s failed", what)
        raise HTTPException(status_code=500, detail=f"{what} failed: {e}") from e


# ── pages ─────────────────────────────────────────────────────────────────────


def _serve(filename: str) -> FileResponse:
    page = FRONTEND_DIR / filename
    if not page.exists():
        raise HTTPException(status_code=500, detail=f"Frontend file missing: {filename}")
    return FileResponse(page)


PAGES = {
    "/": "index.html", "/analyze": "analyze.html", "/avatar": "avatar.html",
    "/vote": "vote.html", "/history": "history.html", "/bills": "bills.html",
    "/chamber": "chamber.html",
}

for _route, _file in PAGES.items():
    app.add_api_route(_route, (lambda f=_file: _serve(f)), methods=["GET"], include_in_schema=False)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "model": config.MODEL,
        "checker_model": config.CHECKER_MODEL,
    }


# ── analysis ──────────────────────────────────────────────────────────────────


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(config.MAX_SOURCE_BYTES + 1)
    if len(data) > config.MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {config.MAX_SOURCE_BYTES // (1024 * 1024)} MB.",
        )
    return data


@app.post("/api/analyze")
def analyze(
    text: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> JSONResponse:
    """Analyze a bill from exactly one of: pasted text, a URL, or an uploaded PDF."""
    provided = [x for x in (text, url, file) if x is not None and x != ""]
    if len(provided) != 1:
        raise HTTPException(status_code=400, detail="Provide exactly one of: text, url, or file.")

    try:
        if text:
            bill_text = extractors.extract_text(text, source_type="text")
        elif url:
            bill_text = extractors.extract_text(url, source_type="url")
        else:
            bill_text = extractors.extract_text(_read_upload(file), source_type="pdf")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not extract bill text: {e}") from e

    if not bill_text or len(bill_text.strip()) < 100:
        raise HTTPException(
            status_code=400,
            detail=f"Extracted text is too short ({len(bill_text or '')} chars). "
            "The source may not contain a bill or extraction failed.",
        )

    with model_errors("Analysis"):
        analysis = summarizer.analyze_bill(bill_text)

    return JSONResponse({
        "success": True,
        "analysis": analysis.to_dict(),
        "source_chars": len(bill_text),
        "truncated": analysis.metadata.get("truncated", False),
    })


# ── Avatar: profile configuration ────────────────────────────────────────────


class ProfileRequest(BaseModel):
    questionnaire_answers: dict[str, int] = {}
    values: str = Field("", max_length=20_000)


class DelegateRequest(BaseModel):
    issue_area: str = Field(..., max_length=100)
    name: str = Field(..., max_length=200)
    note: str = Field("", max_length=2_000)


class SourceRequest(BaseModel):
    title: str = Field("", max_length=300)
    text: str = Field(..., max_length=50_000)


def _require_profile() -> profile_mod.CitizenProfile:
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="Configure your Avatar first (visit /avatar).")
    return p


@app.get("/api/questionnaire")
def get_questionnaire() -> dict:
    return questionnaire.public_questionnaire()


@app.get("/api/profile")
def get_profile() -> JSONResponse:
    p = profile_mod.load_profile()
    return JSONResponse({"profile": p.to_dict() if p else None})


@app.post("/api/profile")
def put_profile(req: ProfileRequest) -> JSONResponse:
    """Create or update the compass and values. Delegates are managed separately
    (they have asymmetric rate-limits) and are preserved."""
    with storage.lock(profile_mod.PROFILE_LOCK):
        existing = profile_mod.load_profile()
        is_new = existing is None
        p = existing or profile_mod.CitizenProfile()
        p.questionnaire_answers = req.questionnaire_answers or p.questionnaire_answers
        p.compass = questionnaire.score(p.questionnaire_answers)
        p.values = req.values
        profile_mod.save_profile(p)
    audit.log_event(
        "profile_created" if is_new else "profile_updated",
        {"compass": p.compass, "values_len": len(p.values)},
    )
    return JSONResponse({"profile": p.to_dict()})


@app.post("/api/profile/delegates")
def add_delegate(req: DelegateRequest) -> JSONResponse:
    """Add/replace a per-issue delegate. Enters cooling-off before it's followed."""
    with storage.lock(profile_mod.PROFILE_LOCK):
        p = _require_profile()
        try:
            delegate = p.add_delegate(req.issue_area, req.name, req.note)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        profile_mod.save_profile(p)
    audit.log_event("delegate_added", {
        "issue_area": delegate.issue_area,
        "name": delegate.name,
        "active_at": delegate.active_at,
        "cooling_off_seconds": profile_mod.cooling_off_seconds(),
    })
    return JSONResponse({"profile": p.to_dict(), "added": delegate.to_dict()})


@app.delete("/api/profile/delegates/{issue_area}")
def revoke_delegate(issue_area: str) -> JSONResponse:
    """Revoke a per-issue delegate immediately."""
    with storage.lock(profile_mod.PROFILE_LOCK):
        p = _require_profile()
        if not p.revoke_delegate(issue_area):
            raise HTTPException(status_code=404, detail=f"No delegate for issue area '{issue_area}'.")
        profile_mod.save_profile(p)
    audit.log_event("delegate_revoked", {"issue_area": issue_area.strip().lower()})
    return JSONResponse({"profile": p.to_dict()})


@app.post("/api/profile/delegates/{issue_area}/sources")
def add_delegate_source(issue_area: str, req: SourceRequest) -> JSONResponse:
    """Attach a recorded position to a delegate (grounds the Avatar in evidence)."""
    with storage.lock(profile_mod.PROFILE_LOCK):
        p = _require_profile()
        try:
            src = p.add_source(issue_area, req.title, req.text)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        profile_mod.save_profile(p)
    audit.log_event("delegate_source_added", {"issue_area": issue_area.strip().lower(), "title": src["title"]})
    return JSONResponse({"profile": p.to_dict(), "added": src})


@app.delete("/api/profile/delegates/{issue_area}/sources/{source_id}")
def remove_delegate_source(issue_area: str, source_id: str) -> JSONResponse:
    with storage.lock(profile_mod.PROFILE_LOCK):
        p = _require_profile()
        if not p.remove_source(issue_area, source_id):
            raise HTTPException(status_code=404, detail="Source not found.")
        profile_mod.save_profile(p)
    audit.log_event("delegate_source_removed", {"issue_area": issue_area.strip().lower(), "id": source_id})
    return JSONResponse({"profile": p.to_dict()})


# ── Avatar: voting ────────────────────────────────────────────────────────────


class VoteRequest(BaseModel):
    """A bill analysis (as produced by /api/analyze) for the Avatar to vote on."""

    analysis: dict


class OverrideRequest(BaseModel):
    bill_title: str = Field(..., max_length=500)
    section_id: str = Field(..., max_length=200)
    position: Literal["yes", "no", "abstain"]
    previous_position: str = Field("", max_length=20)


def _vote_summary(vote: avatar.AvatarVote) -> dict:
    positions = [v.position for v in vote.section_votes]
    return {
        "bill_title": vote.bill_title,
        "sections": len(positions),
        "yes": positions.count("yes"),
        "no": positions.count("no"),
        "abstain": positions.count("abstain"),
        "divergent": sum(v.divergent for v in vote.section_votes),
        "delegate_drift": sum(v.delegate_drift for v in vote.section_votes),
    }


@app.post("/api/avatar/vote")
def avatar_vote(req: VoteRequest) -> JSONResponse:
    """Cast the citizen's Avatar vote on a bill analysis."""
    p = _require_profile()
    with model_errors("Vote"):
        vote = avatar.cast_vote(req.analysis, p)
    audit.log_event("vote_cast", _vote_summary(vote))
    return JSONResponse({"vote": vote.to_dict()})


@app.post("/api/avatar/override")
def avatar_override(req: OverrideRequest) -> JSONResponse:
    """Record a citizen override of an Avatar section vote."""
    audit.log_event("vote_overridden", {
        "bill_title": req.bill_title,
        "section_id": req.section_id,
        "from": req.previous_position,
        "to": req.position,
    })
    return JSONResponse({"ok": True})


@app.get("/api/audit")
def get_audit(limit: int = 200) -> JSONResponse:
    """The audit log, newest first."""
    return JSONResponse({"events": audit.read_events(limit=max(1, min(limit, 5000)))})


@app.get("/api/audit/verify")
def verify_audit() -> JSONResponse:
    """Recompute the audit hash chain and report whether history was altered."""
    return JSONResponse(audit.verify_chain())


# ── Bill lifecycle ────────────────────────────────────────────────────────────


class ProposeRequest(BaseModel):
    title: str = Field("", max_length=300)
    text: str
    scope: str = "ordinary"
    emergency: bool = False


class VersionRequest(BaseModel):
    text: str
    note: str = Field("", max_length=2_000)


class SimulateRequest(BaseModel):
    count: int = Field(1, ge=0, le=1_000_000)


def _load_bill_or_404(bill_id: str) -> bills_mod.Bill:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    return bill


def _persist_refresh(bill: bills_mod.Bill) -> bills_mod.Bill:
    """Advance the lazy clock on read and persist if it changed state."""
    if bills_mod.refresh_state(bill):
        bills_mod.save_bill(bill)
    return bill


@app.get("/api/bills")
def list_bills() -> JSONResponse:
    out = []
    for b in bills_mod.list_bills():
        with bills_mod.bill_lock(b.id):
            out.append(_persist_refresh(b).to_dict())
    return JSONResponse({"bills": out})


@app.get("/api/bills/{bill_id}")
def get_bill(bill_id: str) -> JSONResponse:
    with bills_mod.bill_lock(bill_id):
        return JSONResponse({"bill": _persist_refresh(_load_bill_or_404(bill_id)).to_dict()})


@app.post("/api/bills")
def propose_bill(req: ProposeRequest) -> JSONResponse:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Bill text is required")
    bill = bills_mod.propose(req.title, req.text, req.scope, emergency=req.emergency)
    bills_mod.save_bill(bill)
    audit.log_event("bill_proposed", {
        "id": bill.id, "title": bill.title, "scope": bill.scope, "emergency": bill.emergency,
    })
    return JSONResponse({"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/versions")
def add_bill_version(bill_id: str, req: VersionRequest) -> JSONResponse:
    with bills_mod.bill_lock(bill_id):
        bill = _load_bill_or_404(bill_id)
        try:
            bills_mod.add_version(bill, req.text, req.note)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        bills_mod.save_bill(bill)
    audit.log_event("bill_version_added", {"id": bill.id, "version": bill.current_version})
    return JSONResponse({"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/open")
def open_bill(bill_id: str) -> JSONResponse:
    with bills_mod.bill_lock(bill_id):
        bill = _load_bill_or_404(bill_id)
        try:
            bills_mod.open_for_endorsement(bill)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        bills_mod.save_bill(bill)
    audit.log_event("bill_opened", {"id": bill.id, "title": bill.title})
    return JSONResponse({"bill": bill.to_dict()})


def _freeze_and_analyze(bill: bills_mod.Bill) -> None:
    """If the bill just crossed its endorsement threshold, freeze it and run the
    neutral analysis once on the frozen text so it's ready when voting opens.
    Caller holds the bill lock."""
    if not bills_mod.freeze_if_threshold_met(bill):
        return
    audit.log_event("bill_frozen", {
        "id": bill.id, "frozen_version": bill.frozen_version,
        "cooling_off_until": bill.cooling_off_until, "emergency": bill.emergency,
    })
    try:
        bill.analysis = summarizer.analyze_bill(bills_mod.frozen_text(bill)).to_dict()
    except Exception:
        log.exception("analysis at freeze failed for %s; will retry at vote time", bill.id)
        bill.analysis = None
        return

    if config.CROSS_BILL_CONFLICT_CHECK:
        try:
            passed = [
                {"id": b.id, "title": b.title,
                 "summary": (b.analysis or {}).get("plain_summary", "") or b.title}
                for b in bills_mod.list_bills()
                if b.state == bills_mod.BillState.PASSED and b.id != bill.id
            ]
            bill.conflicts = conflict.detect_conflicts(bill.analysis, passed)
            if bill.conflicts:
                audit.log_event("bill_conflict_flagged", {"id": bill.id, "count": len(bill.conflicts)})
        except Exception:
            log.exception("conflict check failed for %s", bill.id)
            bill.conflicts = []


@app.post("/api/bills/{bill_id}/endorse")
def endorse_bill(bill_id: str) -> JSONResponse:
    with bills_mod.bill_lock(bill_id):
        bill = _load_bill_or_404(bill_id)
        try:
            bills_mod.endorse(bill, "local")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        audit.log_event("bill_endorsed", {"id": bill.id, "total": bills_mod.total_endorsements(bill)})
        _freeze_and_analyze(bill)
        bills_mod.save_bill(bill)
    return JSONResponse({"bill": bill.to_dict()})


@app.delete("/api/bills/{bill_id}/endorse")
def revoke_bill_endorsement(bill_id: str) -> JSONResponse:
    with bills_mod.bill_lock(bill_id):
        bill = _load_bill_or_404(bill_id)
        if not bills_mod.revoke_endorsement(bill, "local"):
            raise HTTPException(
                status_code=400,
                detail="No revocable endorsement (not endorsed, or the revocation window has passed).",
            )
        bills_mod.save_bill(bill)
    audit.log_event("endorsement_revoked", {"id": bill.id})
    return JSONResponse({"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/simulate-endorsements")
def simulate_endorsements(bill_id: str, req: SimulateRequest) -> JSONResponse:
    """Demo scaffold: add N simulated endorsements so thresholds are reachable
    with one human. Logged, so simulated support is never mistaken for real."""
    with bills_mod.bill_lock(bill_id):
        bill = _load_bill_or_404(bill_id)
        if bill.state != bills_mod.BillState.ENDORSING:
            raise HTTPException(status_code=400, detail="Bill is not open for endorsement")
        bill.simulated_endorsements += req.count
        audit.log_event("endorsements_simulated", {"id": bill.id, "count": req.count})
        _freeze_and_analyze(bill)
        bills_mod.save_bill(bill)
    return JSONResponse({"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/vote")
def vote_on_bill(bill_id: str) -> JSONResponse:
    # The lock also makes a double-click (two concurrent vote requests) safe:
    # the second waits, then sees the bill is no longer VOTING.
    with bills_mod.bill_lock(bill_id):
        bill = _persist_refresh(_load_bill_or_404(bill_id))
        if bill.state != bills_mod.BillState.VOTING:
            raise HTTPException(status_code=400, detail=f"Bill is not open for voting (state: {bill.state}).")
        profile = _require_profile()

        with model_errors("Vote"):
            if bill.analysis is None:
                bill.analysis = summarizer.analyze_bill(bills_mod.frozen_text(bill)).to_dict()
                bills_mod.save_bill(bill)
            av = avatar.cast_vote(bill.analysis, profile)
            pop = population.load_population()
            tags = chamber.tag_sections(bill.analysis)

        missing = chamber.missing_tags(bill.analysis, tags)
        if missing:
            # Without a tag the whole population abstains and the operator alone
            # would decide the section. Refuse rather than record that result.
            raise HTTPException(
                status_code=502,
                detail=f"The chamber could not tag section(s) {', '.join(missing)}; no result was recorded. Retry the vote.",
            )

        bill.vote = av.to_dict()
        tallies = chamber.aggregate(bill.analysis, bill.vote, pop, tags)
        bill.result = bills_mod.compute_result(tallies, config.SECTION_PASSAGE_RATIO)
        bill.result["population_size"] = len(pop) + 1  # + the operator
        bill.result["section_tags"] = tags
        bill.state = bills_mod.BillState.PASSED if bill.result["passed"] else bills_mod.BillState.FAILED
        bills_mod.save_bill(bill)

    audit.log_event("bill_voted", {
        "id": bill.id, "title": bill.title,
        "population": bill.result["population_size"],
        "sections_passed": bill.result["sections_passed"],
        "sections_total": bill.result["sections_total"],
        "operator_vote": _vote_summary(av),
    })
    audit.log_event("bill_passed" if bill.result["passed"] else "bill_failed", {"id": bill.id, "title": bill.title})
    return JSONResponse({"bill": bill.to_dict()})


# ── Proxy Chamber: population ─────────────────────────────────────────────────


class RegeneratePopulationRequest(BaseModel):
    size: Optional[int] = Field(None, ge=1, le=100_000)


@app.get("/api/population")
def get_population() -> JSONResponse:
    return JSONResponse(population.distribution())


@app.post("/api/population/regenerate")
def regenerate_population(req: RegeneratePopulationRequest) -> JSONResponse:
    with storage.lock("population"):
        citizens = population.generate_population(req.size)
    audit.log_event("population_generated", {"size": len(citizens)})
    return JSONResponse(population.distribution(citizens))


# ── entry point ───────────────────────────────────────────────────────────────


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    """Entry point for `python main.py`."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")

    if not _is_loopback(host) and not config.ACCESS_TOKEN:
        config.ACCESS_TOKEN = secrets.token_urlsafe(18)
        print("Network-reachable bind without AVATAR_ACCESS_TOKEN: generated a token for this run.")

    suffix = f"/?token={config.ACCESS_TOKEN}" if config.ACCESS_TOKEN else ""
    print(f"Starting Avatar Democracy on http://{host}:{port}{suffix}")
    if config.ACCESS_TOKEN and not _is_loopback(host):
        print(f"From another device use http://<this-PC-IP>:{port}{suffix}")
    print(f"API key configured: {bool(os.environ.get('ANTHROPIC_API_KEY'))}")
    print(f"Models: {config.MODEL} (checker {config.CHECKER_MODEL})")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
