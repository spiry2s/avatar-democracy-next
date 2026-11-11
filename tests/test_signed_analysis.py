"""Issue #7: /api/avatar/vote trusts only analyses this server signed.

The analysis comes back from the browser, and its "grounded" marks and model
comparison decide whether the Avatar may vote yes or no. Unsigned or altered
analyses get an all-abstain vote with no model call.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from backend import avatar, signing
from backend.profile import CitizenProfile

BASE = "http://127.0.0.1:8000"


def _analysis() -> dict[str, Any]:
    return {
        "title": "Grain tax",
        "plain_summary": "It taxes imported grain.",
        "sections": [
            {"section_id": "s1", "heading": "Tax", "summary": "Taxes grain.", "grounded": False,
             "grounding_reason": "quote_not_in_source"},
            {"section_id": "s2", "heading": "Rate", "summary": "Sets the rate.", "grounded": True},
        ],
        "beneficiaries": {"benefits": [], "costs": [], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {"estimated_pages": 2, "confidence_score": 1.0, "ratio": 0.25},
    }


def _browser_round_trip(obj: dict[str, Any]) -> dict[str, Any]:
    """What JSON.parse then JSON.stringify does: 1.0 comes back as 1."""
    def js(value):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {k: js(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [js(v) for v in value]
        return value

    return js(json.loads(json.dumps(obj)))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setattr(main.audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(main.profile_mod, "load_profile", lambda: CitizenProfile(values="v"))
    main.reset_rate_limits()
    return TestClient(main.app, base_url=BASE)


@pytest.fixture
def voted(monkeypatch):
    """Replace the model-backed vote; record whether it ran."""
    ran: list[dict[str, Any]] = []

    def fake_cast_vote(analysis, profile):
        ran.append(analysis)
        return avatar.AvatarVote(
            bill_title=analysis.get("title", ""),
            section_votes=[avatar.SectionVote("s2", "Rate", "yes", "values", "ok", "high")],
            recommendation="ok",
            would_pass_sections=["s2"],
            key_tension="",
        )

    monkeypatch.setattr(main.avatar, "cast_vote", fake_cast_vote)
    return ran


# ── signing itself ───────────────────────────────────────────────────────────


def test_signature_survives_a_browser_round_trip():
    signed = signing.sign(_analysis())
    assert signing.verify(_browser_round_trip(signed))


@pytest.mark.parametrize("tamper", [
    lambda a: a["sections"][0].update(grounded=True),       # mark an unverified claim verified
    lambda a: a.pop("model_comparison", None) or a.update(title="Other"),
    lambda a: a["sections"].append({"section_id": "s3", "heading": "Rider", "summary": "x"}),
    lambda a: a.update(server_signature="0" * 64),
    lambda a: a.pop("server_signature"),
])
def test_any_change_breaks_the_signature(tamper):
    signed = signing.sign(_analysis())
    tamper(signed)
    assert not signing.verify(signed)


# ── the API ──────────────────────────────────────────────────────────────────


def test_analyze_returns_a_signed_analysis_that_votes_normally(client, voted, monkeypatch):
    monkeypatch.setattr(main.comparison, "analyze_for_api", lambda text: _analysis())

    analyzed = client.post("/api/analyze", files={"text": (None, "Section 1. " + "Text. " * 30)})
    assert analyzed.status_code == 200
    analysis = analyzed.json()["analysis"]
    assert signing.verify(analysis)

    vote = client.post("/api/avatar/vote", json={"analysis": _browser_round_trip(analysis)})
    assert vote.status_code == 200
    assert len(voted) == 1  # the real vote path ran


def test_forged_grounding_marks_get_an_abstain_vote_without_a_model_call(client, voted):
    forged = signing.sign(_analysis())
    forged["sections"][0]["grounded"] = True  # claim the unverified section was verified

    response = client.post("/api/avatar/vote", json={"analysis": forged})

    assert response.status_code == 200
    vote = response.json()["vote"]
    assert voted == []
    assert {v["position"] for v in vote["section_votes"]} == {"abstain"}
    assert all(v["basis"] == "unverified" for v in vote["section_votes"])
    assert "could not be verified" in vote["blocking_reason"]


def test_unsigned_analysis_is_logged_and_abstains(client, voted):
    response = client.post("/api/avatar/vote", json={"analysis": _analysis()})
    assert response.status_code == 200
    assert voted == []
    log = main.audit.AUDIT_FILE.read_text(encoding="utf-8")
    assert "vote_unverified_analysis" in log


def test_schema_and_size_checks_still_run_first(client, voted):
    garbage = client.post("/api/avatar/vote", json={"analysis": {"title": "Only a title"}})
    assert garbage.status_code == 400


# ── the key ──────────────────────────────────────────────────────────────────


def test_key_file_is_created_once_and_reused(monkeypatch, tmp_path):
    monkeypatch.delenv("AVATAR_SIGNING_KEY", raising=False)
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(signing, "_cached_key", None)
    monkeypatch.setattr(signing.secrets, "token_bytes", lambda n: b"\n" * n)  # Windows text-mode trap

    signed = signing.sign(_analysis())
    key_file = tmp_path / signing.KEY_FILE
    assert key_file.stat().st_size == 32

    monkeypatch.setattr(signing, "_cached_key", None)  # as after a restart
    assert signing.verify(signed)


def test_short_env_key_leaves_the_analysis_unsigned_but_usable(client, monkeypatch):
    monkeypatch.setenv("AVATAR_SIGNING_KEY", "too-short")
    monkeypatch.setattr(main.comparison, "analyze_for_api", lambda text: _analysis())

    analyzed = client.post("/api/analyze", files={"text": (None, "Section 1. " + "Text. " * 30)})

    assert analyzed.status_code == 200
    assert "server_signature" not in analyzed.json()["analysis"]
