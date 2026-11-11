"""Issue #8: corrupt state files are reported, never overwritten; writes are atomic."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import main
from backend import bills as bills_mod, population, profile, storage

BASE = "http://127.0.0.1:8000"
TRUNCATED = '{"values": "my carefully written values", "delegates": ['


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setattr(bills_mod, "BILLS_DIR", tmp_path / "bills")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(population, "POPULATION_FILE", tmp_path / "population.json")
    monkeypatch.setattr(main.audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    return tmp_path


@pytest.fixture
def client(state):
    return TestClient(main.app, base_url=BASE)


# ── storage primitives ───────────────────────────────────────────────────────


def test_write_json_round_trips_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "x.json"
    storage.write_json(path, {"a": 1})
    assert storage.read_json(path) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_failed_write_keeps_the_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    storage.write_json(path, {"version": 1})

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage.json, "dump", explode)
    with pytest.raises(OSError):
        storage.write_json(path, {"version": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_read_json_missing_is_none_and_corrupt_raises(tmp_path):
    assert storage.read_json(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(storage.CorruptStateError):
        storage.read_json(bad)


# ── profile: the reported data-loss case ─────────────────────────────────────


def test_corrupt_profile_is_reported_and_not_overwritten(client, state):
    path = profile.PROFILE_FILE
    path.write_text(TRUNCATED, encoding="utf-8")

    response = client.post("/api/profile", json={"values": "new"})

    assert response.status_code == 500
    assert response.json()["code"] == "corrupt_state"
    assert "left untouched" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == TRUNCATED


def test_corrupt_profile_blocks_reads_and_delegate_edits(client, state):
    profile.PROFILE_FILE.write_text(TRUNCATED, encoding="utf-8")
    assert client.get("/api/profile").status_code == 500
    added = client.post("/api/profile/delegates", json={"issue_area": "climate", "name": "X"})
    assert added.status_code == 500
    assert profile.PROFILE_FILE.read_text(encoding="utf-8") == TRUNCATED


def test_profile_that_is_not_an_object_counts_as_corrupt(client, state):
    profile.PROFILE_FILE.write_text("[1, 2, 3]", encoding="utf-8")
    assert client.post("/api/profile", json={"values": "new"}).status_code == 500
    assert profile.PROFILE_FILE.read_text(encoding="utf-8") == "[1, 2, 3]"


def test_missing_profile_is_still_created(client, state):
    response = client.post("/api/profile", json={"values": "privacy first"})
    assert response.status_code == 200
    assert storage.read_json(profile.PROFILE_FILE)["values"] == "privacy first"


# ── bills ────────────────────────────────────────────────────────────────────


def test_corrupt_bill_is_reported_not_not_found(client, state):
    bill = bills_mod.propose("B", "x" * 200)
    bills_mod.save_bill(bill)
    path = bills_mod.BILLS_DIR / f"{bill.id}.json"
    path.write_text("{", encoding="utf-8")

    response = client.get(f"/api/bills/{bill.id}")
    assert response.status_code == 500
    assert response.json()["code"] == "corrupt_state"
    assert path.read_text(encoding="utf-8") == "{"


def test_bill_list_skips_a_corrupt_file(client, state):
    good = bills_mod.propose("Good", "x" * 200)
    bad = bills_mod.propose("Bad", "x" * 200)
    bills_mod.save_bill(good)
    bills_mod.save_bill(bad)
    (bills_mod.BILLS_DIR / f"{bad.id}.json").write_text("{", encoding="utf-8")

    response = client.get("/api/bills")
    assert response.status_code == 200
    assert [b["id"] for b in response.json()["bills"]] == [good.id]


@pytest.mark.parametrize("bill_id", ["..", "bill-..", "bill-x\\..\\..\\profile", "BILL-ABC", "profile"])
def test_bill_ids_outside_the_issued_format_are_not_files(state, bill_id):
    assert bills_mod.load_bill(bill_id) is None


# ── population ───────────────────────────────────────────────────────────────


def test_corrupt_population_is_set_aside_then_regenerated(state, monkeypatch):
    monkeypatch.setattr(main.config, "POPULATION_SIZE", 5)
    population.POPULATION_FILE.write_text("{", encoding="utf-8")

    citizens = population.load_population()

    assert len(citizens) == 5
    kept = [p for p in state.iterdir() if p.name.startswith("population.json.corrupt-")]
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "{"
