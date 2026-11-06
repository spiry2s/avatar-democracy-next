"""Compass access, scale, and per-invite persistence.

No network and no provider calls. Invite mode keeps each invite's compass
out of the shared profile file and out of another invite's audit log.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import main
from backend import audit, invites, profile as profile_mod
from backend.questionnaire import clean_answers, score

LAN = "lan-access-token-value"


@pytest.fixture(autouse=True)
def invite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setattr(profile_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(profile_mod, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(audit, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    main.reset_rate_limits()
    invites.close_store()
    yield
    invites.close_store()


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")


def _client() -> TestClient:
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def _redeem(client: TestClient, issued: invites.IssuedInvite) -> None:
    response = client.get("/", params={"token": issued.token}, follow_redirects=False)
    assert response.status_code == 303
    assert issued.token not in response.headers["location"]


def test_score_stays_inside_the_scale_and_rejects_other_values():
    leaned = score({"econ_1": 2})
    assert leaned["economic"] == 1.0
    assert all(-1.0 <= value <= 1.0 for value in leaned.values())
    opposed = score({"econ_1": 2, "econ_2": 2})
    assert opposed["economic"] == 0.0
    assert score({})["liberty"] == 0.0
    with pytest.raises(ValueError):
        clean_answers({"econ_1": 3})
    with pytest.raises(ValueError):
        clean_answers({"not_a_question": 1})
    with pytest.raises(ValueError):
        clean_answers({"econ_1": True})


def test_local_mode_still_uses_one_profile():
    first = _client()
    saved = first.post("/api/profile", json={
        "questionnaire_answers": {"soc_1": -1},
        "values": "local-shared",
    })
    assert saved.status_code == 200
    second = _client()
    profile = second.get("/api/profile").json()["profile"]
    assert profile["values"] == "local-shared"
    assert profile["questionnaire_answers"]["soc_1"] == -1


def test_invites_do_not_share_a_compass(monkeypatch, tmp_path):
    _enable(monkeypatch)
    shared = tmp_path / "profile.json"
    shared.write_text(json.dumps({
        "citizen_id": "local",
        "created_at": 0,
        "updated_at": 0,
        "compass": {"economic": 1},
        "questionnaire_answers": {"econ_1": 2},
        "values": "SHARED-SENTINEL",
        "delegates": [],
    }), encoding="utf-8")

    ada = invites.create_invite(label="ada")
    bea = invites.create_invite(label="bea")
    ada_client = _client()
    bea_client = _client()
    _redeem(ada_client, ada)
    _redeem(bea_client, bea)

    assert ada_client.get("/api/profile").json()["profile"] is None
    assert bea_client.get("/api/profile").json()["profile"] is None
    assert "SHARED-SENTINEL" not in ada_client.get("/avatar").text

    saved = ada_client.post("/api/profile", json={
        "questionnaire_answers": {"econ_1": 2, "lib_1": -2},
        "values": "Ada keeps this",
    })
    assert saved.status_code == 200
    profile = saved.json()["profile"]
    assert profile["values"] == "Ada keeps this"
    assert profile["questionnaire_answers"] == {"econ_1": 2, "lib_1": -2}
    assert profile["compass"]["economic"] == 1.0
    assert profile["compass"]["liberty"] == -1.0
    assert all(-1.0 <= value <= 1.0 for value in profile["compass"].values())
    assert profile["citizen_id"] == ada.invite.id
    assert "SHARED-SENTINEL" not in json.dumps(profile)

    refreshed = ada_client.get("/api/profile").json()["profile"]
    assert refreshed["questionnaire_answers"] == profile["questionnaire_answers"]
    assert refreshed["compass"] == profile["compass"]
    assert refreshed["values"] == "Ada keeps this"

    assert bea_client.get("/api/profile").json()["profile"] is None
    bea_audit = json.dumps(bea_client.get("/api/audit").json())
    assert "Ada keeps this" not in bea_audit
    assert ada.invite.id not in bea_audit
    ada_audit = ada_client.get("/api/audit").json()["events"]
    assert any(event["event_type"] in {"profile_created", "profile_updated"} for event in ada_audit)
    assert "Ada keeps this" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8") if (tmp_path / "audit.jsonl").exists() else True
    assert "Ada keeps this" not in shared.read_text(encoding="utf-8")

    rejected = ada_client.post("/api/profile", json={
        "questionnaire_answers": {"econ_1": 9},
        "values": "should not stick",
    })
    assert rejected.status_code == 400
    kept = ada_client.get("/api/profile").json()["profile"]
    assert kept["values"] == "Ada keeps this"
    assert kept["questionnaire_answers"]["econ_1"] == 2

    cleared = ada_client.post("/api/profile", json={
        "questionnaire_answers": {},
        "values": "Ada keeps this",
    })
    assert cleared.status_code == 200
    empty = cleared.json()["profile"]
    assert empty["questionnaire_answers"] == {}
    assert all(value == 0.0 for value in empty["compass"].values())
    assert ada_client.get("/api/profile").json()["profile"]["questionnaire_answers"] == {}


def test_compass_page_and_api_stay_behind_the_invite(monkeypatch):
    _enable(monkeypatch)
    stranger = _client()
    assert stranger.get("/avatar").status_code == 403
    assert stranger.get("/api/questionnaire").status_code == 403
    assert stranger.get("/api/profile").status_code == 403
    issued = invites.create_invite(label="ada")
    _redeem(stranger, issued)
    page = stranger.get("/avatar")
    assert page.status_code == 200
    assert 'href="/avatar#compass"' in page.text
    assert "not a validated psychometric test" in page.text
    questions = stranger.get("/api/questionnaire").json()
    assert len(questions["questions"]) == 12
    assert set(questions["axes"]) == {
        "economic", "social", "liberty", "environment", "foreign", "governance",
    }
    assert "direction" not in json.dumps(questions["questions"])
