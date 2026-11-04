"""End-to-end API tests with a fake model (no network, no API key)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from backend import audit, config, profile


@pytest.fixture
def client(state_dir, fake_llm):
    return TestClient(main.app)


def _make_profile(client):
    r = client.post("/api/profile", json={"questionnaire_answers": {"lib_1": 2}, "values": "privacy first"})
    assert r.status_code == 200


def _bill_to_voting(client) -> str:
    r = client.post("/api/bills", json={"title": "Privacy", "text": "x" * 200})
    bid = r.json()["bill"]["id"]
    assert client.post(f"/api/bills/{bid}/open").status_code == 200
    r = client.post(f"/api/bills/{bid}/simulate-endorsements", json={"count": 1000})
    assert r.json()["bill"]["state"] == "cooling_off"
    r = client.get(f"/api/bills/{bid}")  # cooling-off is 0s -> lazy clock promotes
    assert r.json()["bill"]["state"] == "voting"
    return bid


def test_full_lifecycle_vote(client, fake_llm):
    _make_profile(client)
    bid = _bill_to_voting(client)

    r = client.post(f"/api/bills/{bid}/vote")
    assert r.status_code == 200, r.text
    bill = r.json()["bill"]
    result = bill["result"]
    assert result["population_size"] == config.POPULATION_SIZE + 1
    # Every section was tagged, so the population (not just the operator) voted on it.
    assert all(t["tagged"] for t in result["per_section"])
    sec2 = next(s for s in result["per_section"] if s["section_id"] == "Sec. 2")
    assert sec2["yes"] + sec2["no"] + sec2["abstain"] == config.POPULATION_SIZE + 1
    # The checker disagreed on Sec. 2 -> flagged divergent and low-confidence.
    votes = {v["section_id"]: v for v in bill["vote"]["section_votes"]}
    assert votes["Sec. 2"]["divergent"] and votes["Sec. 2"]["confidence"] == "low"
    assert not votes["Sec. 1"]["divergent"]
    assert {"analysis", "vote", "cross_check", "tag_sections"} <= set(fake_llm.names())
    assert audit.verify_chain()["ok"]


def test_vote_schema_pins_section_ids(client, fake_llm):
    _make_profile(client)
    bid = _bill_to_voting(client)
    client.post(f"/api/bills/{bid}/vote")
    vote_call = next(c for c in fake_llm.calls if c["name"] == "vote")
    sid = vote_call["schema"]["properties"]["section_votes"]["items"]["properties"]["section_id"]
    assert sid["enum"] == ["Sec. 1", "Sec. 2"]


def test_untagged_section_refuses_to_record_result(client, fake_llm):
    _make_profile(client)
    bid = _bill_to_voting(client)
    fake_llm.responses["tag_sections"] = {"tags": [
        {"section_id": "Sec. 1", "economic": 0, "social": 0, "liberty": 0,
         "environment": 0, "foreign": 0, "governance": 0},
    ]}
    r = client.post(f"/api/bills/{bid}/vote")
    assert r.status_code == 502
    assert "Sec. 2" in r.json()["detail"]
    bill = client.get(f"/api/bills/{bid}").json()["bill"]
    assert bill["state"] == "voting" and bill["result"] is None


def test_second_vote_rejected(client):
    _make_profile(client)
    bid = _bill_to_voting(client)
    assert client.post(f"/api/bills/{bid}/vote").status_code == 200
    assert client.post(f"/api/bills/{bid}/vote").status_code == 400


def test_corrupt_profile_is_never_overwritten(client, state_dir):
    path = profile.PROFILE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    r = client.post("/api/profile", json={"values": "new"})
    assert r.status_code == 500
    assert "corrupt" in r.json()["detail"]
    assert path.read_text(encoding="utf-8") == "{not json"


def test_cross_origin_post_refused(client):
    r = client.post("/api/bills", json={"text": "x" * 200}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_post_allowed(client):
    r = client.post("/api/bills", json={"text": "x" * 200}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_access_token_enforced(client, monkeypatch):
    monkeypatch.setattr(config, "ACCESS_TOKEN", "s3cret")
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers={"X-Access-Token": "wrong"}).status_code == 401
    assert client.get("/api/health", headers={"X-Access-Token": "s3cret"}).status_code == 200
    # First visit with ?token= sets a cookie that authorizes later requests.
    assert client.get("/?token=s3cret").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_analyze_reports_truncation(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_BILL_CHARS", 150)
    r = client.post("/api/analyze", data={"text": "word " * 100})
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["analysis"]["metadata"]["total_chars"] == 500
    assert "NOT reviewed" in body["analysis"]["open_questions"][0]


def test_upload_size_cap(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_SOURCE_BYTES", 10)
    r = client.post("/api/analyze", files={"file": ("bill.pdf", b"%PDF-" + b"0" * 100, "application/pdf")})
    assert r.status_code == 413


def test_override_validates_position(client):
    r = client.post("/api/avatar/override", json={"bill_title": "t", "section_id": "s", "position": "maybe"})
    assert r.status_code == 422


def test_bad_bill_id_is_404_not_path_traversal(client):
    assert client.get("/api/bills/..%2F..%2Fprofile").status_code == 404
    assert client.get("/api/bills/bill-zzz!").status_code == 404
