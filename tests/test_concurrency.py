"""Issue #3: a model call must not stall other requests, and state edits must not race."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

import main
from backend import bills as bills_mod, population, profile

BASE = "http://127.0.0.1:8000"


@pytest.fixture(autouse=True)
def _no_invites(monkeypatch):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)


def test_slow_analysis_does_not_block_health(monkeypatch):
    def slow_analysis(text):
        time.sleep(1.5)
        return {"title": "t"}

    monkeypatch.setattr(main.comparison, "analyze_for_api", slow_analysis)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            start = time.perf_counter()

            async def analyze():
                response = await client.post("/api/analyze", files={"text": (None, "x" * 200)})
                return response.status_code

            async def health():
                await asyncio.sleep(0.2)
                response = await client.get("/api/health")
                return response.status_code, time.perf_counter() - start

            return await asyncio.gather(analyze(), health())

    analyzed, (health_status, answered_at) = asyncio.run(run())
    assert analyzed == 200
    assert health_status == 200
    # Sent at 0.2 s. Before the fix it waited for the 1.5 s analysis to finish.
    assert answered_at < 1.0, f"health answered at {answered_at:.2f}s"


def test_concurrent_votes_on_one_bill_vote_once(tmp_path, monkeypatch):
    monkeypatch.setattr(bills_mod, "BILLS_DIR", tmp_path / "bills")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(population, "POPULATION_FILE", tmp_path / "population.json")
    monkeypatch.setattr(main.audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    profile.save_profile(profile.CitizenProfile(values="v"))
    population.save_population([{"archetype": "a", "compass": {"liberty": 0.9}}] * 10)

    bill = bills_mod.propose("B", "x" * 200)
    bill.state = bills_mod.BillState.VOTING
    bill.analysis = {"title": "B", "sections": [{"section_id": "S1", "heading": "H", "grounded": True}]}
    bills_mod.save_bill(bill)

    calls = []
    calls_lock = threading.Lock()

    class Vote:
        def to_dict(self):
            return {"section_votes": [{"section_id": "S1", "position": "yes"}]}

    def slow_vote(analysis, prof):
        with calls_lock:
            calls.append(1)
        time.sleep(0.5)
        return Vote()

    monkeypatch.setattr(main.avatar, "cast_vote", slow_vote)
    monkeypatch.setattr(main.chamber, "tag_sections", lambda analysis: {"S1": {"liberty": 0.9}})

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await asyncio.gather(
                client.post(f"/api/bills/{bill.id}/vote"),
                client.post(f"/api/bills/{bill.id}/vote"),
            )

    first, second = asyncio.run(run())
    assert sorted([first.status_code, second.status_code]) == [200, 400]
    assert len(calls) == 1, "the second request must see the bill already decided"
