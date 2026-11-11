"""Issue #4: bills stay shared, but each invitee endorses as themselves.

Every endorsement used to be recorded as citizen "local", so ten testers
endorsing one bill counted once, and any tester could revoke it for all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from backend import audit, bills as bills_mod, invites, profile as profile_mod, tenant


@pytest.fixture(autouse=True)
def invite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setattr(profile_mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(profile_mod, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(audit, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(bills_mod, "BILLS_DIR", tmp_path / "bills")
    main.reset_rate_limits()
    invites.close_store()
    yield
    invites.close_store()


def _tester(label: str) -> tuple[TestClient, invites.IssuedInvite]:
    issued = invites.create_invite(label=label)
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert client.get("/", params={"token": issued.token}, follow_redirects=False).status_code == 303
    return client, issued


def _open_bill(client: TestClient) -> str:
    bill_id = client.post("/api/bills", json={"title": "Shared", "text": "x" * 200}).json()["bill"]["id"]
    assert client.post(f"/api/bills/{bill_id}/open").status_code == 200
    return bill_id


def test_each_tester_endorsement_counts_separately():
    ada, _ = _tester("ada")
    bea, _ = _tester("bea")
    bill_id = _open_bill(ada)

    assert ada.post(f"/api/bills/{bill_id}/endorse").status_code == 200
    second = bea.post(f"/api/bills/{bill_id}/endorse")

    assert second.status_code == 200
    assert second.json()["bill"]["total_endorsements"] == 2


def test_a_tester_cannot_revoke_someone_elses_endorsement():
    ada, ada_invite = _tester("ada")
    bea, _ = _tester("bea")
    bill_id = _open_bill(ada)
    ada.post(f"/api/bills/{bill_id}/endorse")

    assert bea.delete(f"/api/bills/{bill_id}/endorse").status_code == 400
    stored = bills_mod.load_bill(bill_id)
    assert [e["citizen_id"] for e in stored.endorsements] == [ada_invite.invite.id]

    assert ada.delete(f"/api/bills/{bill_id}/endorse").status_code == 200
    assert bills_mod.load_bill(bill_id).endorsements == []


def test_sponsor_is_the_proposing_tester_and_bills_stay_shared():
    ada, ada_invite = _tester("ada")
    bea, _ = _tester("bea")
    bill_id = _open_bill(ada)

    assert bills_mod.load_bill(bill_id).sponsor == ada_invite.invite.id
    assert bill_id in bea.get("/api/bills").text


def test_outside_invite_mode_the_citizen_is_local(monkeypatch):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    assert tenant.citizen_id() == "local"
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    bill_id = _open_bill(client)
    client.post(f"/api/bills/{bill_id}/endorse")
    assert [e["citizen_id"] for e in bills_mod.load_bill(bill_id).endorsements] == ["local"]
