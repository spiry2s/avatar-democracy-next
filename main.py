"""
FastAPI server for the Bill Summarizer.

Serves:
- GET /            - Web UI (frontend/index.html)
- POST /api/analyze - Analyze a bill (text, URL, or uploaded PDF)
- GET /api/health  - Health check

Run with:
    python main.py

Or with uvicorn directly:
    uvicorn main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from typing import Optional

load_dotenv(Path(__file__).parent / ".env", override=True)

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import (
    audit,
    avatar,
    bills as bills_mod,
    chamber,
    config,
    comparison,
    conflict,
    extractors,
    llm,
    population,
    profile as profile_mod,
    questionnaire,
)

# Resolve frontend directory relative to project root
PROJECT_ROOT = Path(__file__).parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(
    title="Bill Summarizer",
    description="Plain-language summaries and red-flag detection for legislative bills.",
    version="0.1.0",
)

# Serve frontend static files (CSS, JS) at /static/*
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


class AnalyzeTextRequest(BaseModel):
    """Request body for analyzing pasted text."""

    text: str
    source_url: Optional[str] = None


def _is_model_text_error(exc: ValueError) -> bool:
    """True when the model text failed JSON parsing or the analysis schema.

    A provider ValueError, such as the Anthropic SDK requiring streaming, is
    not model text and must not be reported as unparseable output.
    """
    text = str(exc)
    return text.startswith("Could not parse") or text.startswith("Analysis JSON failed schema")


def _output_status(exc: ValueError) -> int:
    return 502 if _is_model_text_error(exc) else 500


def _output_detail(exc: ValueError) -> str:
    if _is_model_text_error(exc):
        return f"Analysis produced unparseable output: {exc}"
    return f"Analysis failed: {exc}"


def _serve(filename: str) -> FileResponse:
    page = FRONTEND_DIR / filename
    if not page.exists():
        raise HTTPException(status_code=500, detail=f"Frontend file missing: {filename}")
    return FileResponse(page)


@app.get("/")
async def root() -> FileResponse:
    """Serve the landing page."""
    return _serve("index.html")


@app.get("/analyze")
async def analyze_page() -> FileResponse:
    """Serve the bill analyzer UI."""
    return _serve("analyze.html")


@app.get("/avatar")
async def avatar_page() -> FileResponse:
    """Serve the Avatar setup UI."""
    return _serve("avatar.html")


@app.get("/vote")
async def vote_page() -> FileResponse:
    """Serve the voting UI."""
    return _serve("vote.html")


@app.get("/history")
async def history_page() -> FileResponse:
    """Serve the audit-log UI."""
    return _serve("history.html")


@app.get("/bills")
async def bills_page() -> FileResponse:
    """Serve the bill-lifecycle UI."""
    return _serve("bills.html")


@app.get("/chamber")
async def chamber_page() -> FileResponse:
    """Serve the Proxy Chamber population dashboard."""
    return _serve("chamber.html")


@app.get("/manifest.webmanifest")
async def web_manifest() -> FileResponse:
    """Web app manifest so a phone can add the UI to its home screen."""
    path = FRONTEND_DIR / "manifest.webmanifest"
    if not path.exists():
        raise HTTPException(status_code=500, detail="Frontend file missing: manifest.webmanifest")
    return FileResponse(path, media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    """Static-shell service worker. It does not cache /api."""
    path = FRONTEND_DIR / "sw.js"
    if not path.exists():
        raise HTTPException(status_code=500, detail="Frontend file missing: sw.js")
    return FileResponse(
        path,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/health")
async def health() -> dict:
    """Health check. Reports whether the configured LLM provider has a credential.

    With the default Anthropic provider this is whether ANTHROPIC_API_KEY is set.
    Comparison fields name the configured provider and model only. The response
    never includes an API key or any other secret.
    """
    configured = comparison.comparison_configured()
    provider = (config.COMPARISON_PROVIDER or "").strip().lower() if configured else ""
    model = (config.COMPARISON_MODEL or "").strip() if configured else ""
    return {
        "status": "ok",
        "api_key_configured": llm.credentials_configured(),
        "comparison_configured": configured,
        "comparison_provider": provider,
        "comparison_model": model,
        "comparison_key_configured": bool(provider) and llm.credentials_configured(provider),
    }


@app.post("/api/analyze")
async def analyze(
    text: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> JSONResponse:
    """
    Analyze a bill from text, URL, or uploaded PDF.

    Provide exactly one of:
    - text: Pasted bill text
    - url: URL to a bill (PDF or HTML)
    - file: Uploaded PDF

    Returns the structured analysis as JSON.
    """
    # Count provided inputs
    sources_provided = sum(x is not None and x != "" for x in [text, url, file])

    if sources_provided == 0:
        raise HTTPException(
            status_code=400,
            detail="Provide one of: text, url, or file",
        )
    if sources_provided > 1:
        raise HTTPException(
            status_code=400,
            detail="Provide only one of: text, url, or file (not multiple)",
        )

    # Extract text from whichever source was provided
    try:
        if text:
            bill_text = extractors.extract_text(text, source_type="text")
        elif url:
            bill_text = extractors.extract_text(url, source_type="url")
        elif file:
            content = await file.read()
            bill_text = extractors.extract_text(content, source_type="pdf")
        else:
            # Should never reach here due to checks above
            raise HTTPException(status_code=400, detail="No source provided")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract bill text: {str(e)}",
        ) from e

    if not bill_text or len(bill_text.strip()) < 100:
        raise HTTPException(
            status_code=400,
            detail=f"Extracted text is too short ({len(bill_text)} chars). "
            "The source may not contain a bill or extraction failed.",
        )

    # Run the analysis
    try:
        analysis = comparison.analyze_for_api(bill_text)
    except llm.LLMTruncatedError as e:
        # A cut-off reply is not unparseable JSON. Say so.
        raise HTTPException(status_code=502, detail=str(e)) from e
    except RuntimeError as e:
        # Configuration errors (missing API key)
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=_output_status(e), detail=_output_detail(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(e)}",
        ) from e

    body: dict = {
        "success": True,
        "analysis": analysis,
        "source_chars": len(bill_text),
    }
    # Same object as analysis["model_comparison"] when a second family ran.
    # Omitted entirely when comparison is not configured.
    if isinstance(analysis, dict) and "model_comparison" in analysis:
        body["model_comparison"] = analysis["model_comparison"]
    return JSONResponse(content=body)


# ── Avatar: profile configuration ────────────────────────────────────────────


class ProfileRequest(BaseModel):
    """Create or update the citizen's compass + values."""

    questionnaire_answers: dict[str, int] = {}
    values: str = ""


class DelegateRequest(BaseModel):
    issue_area: str
    name: str
    note: str = ""


@app.get("/api/questionnaire")
async def get_questionnaire() -> dict:
    """The seeding questionnaire (statements + scale + axis labels)."""
    return questionnaire.public_questionnaire()


@app.get("/api/profile")
async def get_profile() -> JSONResponse:
    """Return the stored profile, or null if none configured yet."""
    p = profile_mod.load_profile()
    return JSONResponse(content={"profile": p.to_dict() if p else None})


@app.post("/api/profile")
async def put_profile(req: ProfileRequest) -> JSONResponse:
    """
    Create or update the citizen's compass and values. Delegates are managed
    separately (they have asymmetric rate-limits). Preserves existing delegates.
    """
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
    return JSONResponse(content={"profile": p.to_dict()})


@app.post("/api/profile/delegates")
async def add_delegate(req: DelegateRequest) -> JSONResponse:
    """Add/replace a per-issue delegate. Enters cooling-off before it's followed."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="Configure your Avatar before adding delegates.")
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
    return JSONResponse(content={"profile": p.to_dict(), "added": delegate.to_dict()})


@app.delete("/api/profile/delegates/{issue_area}")
async def revoke_delegate(issue_area: str) -> JSONResponse:
    """Revoke a per-issue delegate immediately."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="No profile configured.")
    removed = p.revoke_delegate(issue_area)
    if not removed:
        raise HTTPException(status_code=404, detail=f"No delegate for issue area '{issue_area}'.")

    profile_mod.save_profile(p)
    audit.log_event("delegate_revoked", {"issue_area": issue_area.strip().lower()})
    return JSONResponse(content={"profile": p.to_dict()})


class SourceRequest(BaseModel):
    title: str = ""
    text: str


@app.post("/api/profile/delegates/{issue_area}/sources")
async def add_delegate_source(issue_area: str, req: SourceRequest) -> JSONResponse:
    """Attach a recorded position to a delegate (grounds the Avatar in evidence)."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="No profile configured.")
    try:
        src = p.add_source(issue_area, req.title, req.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    profile_mod.save_profile(p)
    audit.log_event("delegate_source_added", {
        "issue_area": issue_area.strip().lower(), "title": src["title"],
    })
    return JSONResponse(content={"profile": p.to_dict(), "added": src})


@app.delete("/api/profile/delegates/{issue_area}/sources/{source_id}")
async def remove_delegate_source(issue_area: str, source_id: str) -> JSONResponse:
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(status_code=400, detail="No profile configured.")
    if not p.remove_source(issue_area, source_id):
        raise HTTPException(status_code=404, detail="Source not found.")
    profile_mod.save_profile(p)
    return JSONResponse(content={"profile": p.to_dict()})


# ── Avatar: voting ────────────────────────────────────────────────────────────


class VoteRequest(BaseModel):
    """A bill analysis (as produced by /api/analyze) for the Avatar to vote on."""

    analysis: dict


class OverrideRequest(BaseModel):
    bill_title: str
    section_id: str
    position: str
    previous_position: str = ""


@app.post("/api/avatar/vote")
async def avatar_vote(req: VoteRequest) -> JSONResponse:
    """Cast the citizen's Avatar vote on a bill analysis."""
    p = profile_mod.load_profile()
    if p is None:
        raise HTTPException(
            status_code=400,
            detail="Configure your Avatar first (visit /avatar).",
        )

    try:
        vote = avatar.cast_vote(req.analysis, p)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        if _is_model_text_error(e):
            detail = f"Avatar produced unparseable output: {e}"
            status = 502
        else:
            detail = f"Vote failed: {e}"
            status = 500
        raise HTTPException(status_code=status, detail=detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vote failed: {e}") from e

    audit.log_event("vote_cast", {
        "bill_title": vote.bill_title,
        "sections": len(vote.section_votes),
        "yes": sum(1 for v in vote.section_votes if v.position == "yes" and not v.needs_human_review),
        "no": sum(1 for v in vote.section_votes if v.position == "no" and not v.needs_human_review),
        "abstain": sum(1 for v in vote.section_votes if v.position == "abstain"),
        "needs_human_review": sum(
            1 for v in vote.section_votes
            if v.needs_human_review and v.position in ("yes", "no")
        ),
    })
    return JSONResponse(content={"vote": vote.to_dict()})


@app.post("/api/avatar/override")
async def avatar_override(req: OverrideRequest) -> JSONResponse:
    """Record a citizen override of an Avatar section vote."""
    audit.log_event("vote_overridden", {
        "bill_title": req.bill_title,
        "section_id": req.section_id,
        "from": req.previous_position,
        "to": req.position,
    })
    return JSONResponse(content={"ok": True})


@app.get("/api/audit")
async def get_audit(limit: int = 200) -> JSONResponse:
    """Return the audit log, newest first."""
    return JSONResponse(content={"events": audit.read_events(limit=limit)})


# ── Bill lifecycle ────────────────────────────────────────────────────────────


class ProposeRequest(BaseModel):
    title: str
    text: str
    scope: str = "ordinary"
    emergency: bool = False


class VersionRequest(BaseModel):
    text: str
    note: str = ""


class SimulateRequest(BaseModel):
    count: int = 1


def _persist_refresh(bill: bills_mod.Bill) -> bills_mod.Bill:
    """Advance the lazy clock on read and persist if it changed state."""
    if bills_mod.refresh_state(bill):
        bills_mod.save_bill(bill)
    return bill


@app.get("/api/bills")
async def list_bills() -> JSONResponse:
    out = [_persist_refresh(b).to_dict() for b in bills_mod.list_bills()]
    return JSONResponse(content={"bills": out})


@app.get("/api/bills/{bill_id}")
async def get_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    return JSONResponse(content={"bill": _persist_refresh(bill).to_dict()})


@app.post("/api/bills")
async def propose_bill(req: ProposeRequest) -> JSONResponse:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Bill text is required")
    bill = bills_mod.propose(req.title, req.text, req.scope, emergency=req.emergency)
    bills_mod.save_bill(bill)
    audit.log_event("bill_proposed", {
        "id": bill.id, "title": bill.title, "scope": bill.scope, "emergency": bill.emergency,
    })
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/versions")
async def add_bill_version(bill_id: str, req: VersionRequest) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    try:
        bills_mod.add_version(bill, req.text, req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    bills_mod.save_bill(bill)
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/open")
async def open_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    try:
        bills_mod.open_for_endorsement(bill)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    bills_mod.save_bill(bill)
    audit.log_event("bill_opened", {"id": bill.id, "title": bill.title})
    return JSONResponse(content={"bill": bill.to_dict()})


def _freeze_and_analyze(bill: bills_mod.Bill) -> None:
    """If the bill just crossed its endorsement threshold, freeze it and run the
    neutral analysis once on the frozen text so it's ready when voting opens."""
    if bills_mod.freeze_if_threshold_met(bill):
        audit.log_event("bill_frozen", {
            "id": bill.id, "frozen_version": bill.frozen_version,
            "cooling_off_until": bill.cooling_off_until, "emergency": bill.emergency,
        })
        try:
            bill.analysis = comparison.analyze_for_api(bills_mod.frozen_text(bill))
        except Exception:
            bill.analysis = None  # analysis will be retried lazily at vote time

        # Cross-bill conflict: compare against already-passed bills.
        if config.CROSS_BILL_CONFLICT_CHECK and bill.analysis:
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
                bill.conflicts = []


@app.post("/api/bills/{bill_id}/endorse")
async def endorse_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    try:
        bills_mod.endorse(bill, "local")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    audit.log_event("bill_endorsed", {"id": bill.id, "total": bills_mod.total_endorsements(bill)})
    _freeze_and_analyze(bill)
    bills_mod.save_bill(bill)
    return JSONResponse(content={"bill": bill.to_dict()})


@app.delete("/api/bills/{bill_id}/endorse")
async def revoke_bill_endorsement(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    if not bills_mod.revoke_endorsement(bill, "local"):
        raise HTTPException(status_code=400, detail="No revocable endorsement (not endorsed, or the 24h window has passed).")
    bills_mod.save_bill(bill)
    audit.log_event("endorsement_revoked", {"id": bill.id})
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/simulate-endorsements")
async def simulate_endorsements(bill_id: str, req: SimulateRequest) -> JSONResponse:
    """Demo scaffold: add N simulated endorsements so thresholds are reachable
    with one human. The Proxy Chamber phase replaces this with real Avatars."""
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    if bill.state != bills_mod.BillState.ENDORSING:
        raise HTTPException(status_code=400, detail="Bill is not open for endorsement")
    bill.simulated_endorsements += max(0, req.count)
    _freeze_and_analyze(bill)
    bills_mod.save_bill(bill)
    return JSONResponse(content={"bill": bill.to_dict()})


@app.post("/api/bills/{bill_id}/vote")
async def vote_on_bill(bill_id: str) -> JSONResponse:
    bill = bills_mod.load_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    _persist_refresh(bill)
    if bill.state != bills_mod.BillState.VOTING:
        raise HTTPException(status_code=400, detail=f"Bill is not open for voting (state: {bill.state}).")

    profile = profile_mod.load_profile()
    if profile is None:
        raise HTTPException(status_code=400, detail="Configure your Avatar first (visit /avatar).")

    try:
        if bill.analysis is None:
            bill.analysis = comparison.analyze_for_api(bills_mod.frozen_text(bill))
        # The operator's own Avatar votes through the full engine (values + delegates).
        av = avatar.cast_vote(bill.analysis, profile)
        bill.vote = av.to_dict()
        # The Proxy Chamber: one axis-tagging call, then the whole population votes
        # deterministically. Operator's position is folded into the tally.
        pop = population.load_population()
        tags = chamber.tag_sections(bill.analysis)
        tallies = chamber.aggregate(bill.analysis, bill.vote, pop, tags)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        if _is_model_text_error(e):
            detail = f"Produced unparseable output: {e}"
            status = 502
        else:
            detail = f"Vote failed: {e}"
            status = 500
        raise HTTPException(status_code=status, detail=detail) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Vote failed: {e}") from e

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
    })
    audit.log_event("bill_passed" if bill.result["passed"] else "bill_failed",
                    {"id": bill.id, "title": bill.title})
    return JSONResponse(content={"bill": bill.to_dict()})


# ── Proxy Chamber: population ─────────────────────────────────────────────────


class RegeneratePopulationRequest(BaseModel):
    size: Optional[int] = None


@app.get("/api/population")
async def get_population() -> JSONResponse:
    """Synthetic population: size, archetypes, per-axis means, and compasses."""
    return JSONResponse(content=population.distribution())


@app.post("/api/population/regenerate")
async def regenerate_population(req: RegeneratePopulationRequest) -> JSONResponse:
    citizens = population.generate_population(req.size)
    audit.log_event("population_generated", {"size": len(citizens)})
    return JSONResponse(content=population.distribution(citizens))


def ensure_port_available(host: str, port: int) -> None:
    """Exit before the startup banner when the bind port is already taken."""
    import errno
    import socket
    import sys

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"Port {port} on {host} is already in use. "
                "Set PORT to a free port and start again.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise
    finally:
        sock.close()


def resolve_bind_host(argv: list[str] | None = None) -> str:
    """Address ``python main.py`` binds.

    ``--host`` wins when it is passed. Otherwise the HOST environment variable
    is used. When neither is set, the default stays ``127.0.0.1`` (localhost).
    ``0.0.0.0`` is opt-in and exposes the app to the local network.
    """
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default=None)
    args, _unknown = parser.parse_known_args([] if argv is None else argv)
    if args.host:
        return args.host
    return os.environ.get("HOST", "127.0.0.1")


def main() -> None:
    """Entry point for `python main.py`."""
    import sys
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = resolve_bind_host(sys.argv[1:])
    ensure_port_available(host, port)

    print(f"Starting Bill Summarizer on http://{host}:{port}")
    print(f"LLM provider: {config.LLM_PROVIDER} (primary model {config.MODEL})")
    print(f"API key configured: {llm.credentials_configured()}")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
