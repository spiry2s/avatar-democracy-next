"""Issue #13: the audit log is hash-chained, so altered history is evident."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

import main
from backend import audit


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit, "AUDIT_FILE", path)
    monkeypatch.setattr(audit, "_tails", {})
    return path


def _write(n: int) -> None:
    for i in range(n):
        audit.log_event("vote_cast", {"i": i, "note": "überprüft"})


def _lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _save(path, lines) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_untouched_log_verifies(log_file):
    _write(3)
    result = audit.verify_chain()
    assert result["ok"] is True
    assert (result["events"], result["verified"], result["legacy"]) == (3, 3, 0)
    assert result["head"] == json.loads(_lines(log_file)[-1])["hash"]


def test_empty_log_verifies(log_file):
    assert audit.verify_chain()["ok"] is True


def test_edited_event_is_detected(log_file):
    _write(3)
    lines = _lines(log_file)
    event = json.loads(lines[1])
    event["details"]["i"] = 99
    lines[1] = json.dumps(event)
    _save(log_file, lines)

    result = audit.verify_chain()
    assert result["ok"] is False and result["broken_at"] == 2
    assert "content" in result["reason"]


def test_deleted_event_is_detected(log_file):
    _write(3)
    lines = _lines(log_file)
    _save(log_file, [lines[0], lines[2]])
    result = audit.verify_chain()
    assert result["ok"] is False and result["broken_at"] == 2


def test_reordered_events_are_detected(log_file):
    _write(3)
    lines = _lines(log_file)
    _save(log_file, [lines[0], lines[2], lines[1]])
    assert audit.verify_chain()["broken_at"] == 2


def test_forged_event_with_its_own_valid_hash_is_detected(log_file):
    _write(2)
    lines = _lines(log_file)
    forged = {"timestamp": 1.0, "event_type": "vote_cast", "details": {"i": "forged"}, "prev_hash": audit.GENESIS}
    forged["hash"] = audit._digest(audit.GENESIS, 1.0, "vote_cast", {"i": "forged"})
    _save(log_file, [lines[0], json.dumps(forged), lines[1]])
    result = audit.verify_chain()
    assert result["ok"] is False and result["broken_at"] == 2
    assert "previous-hash" in result["reason"]


def test_garbage_line_is_detected(log_file):
    _write(2)
    lines = _lines(log_file)
    _save(log_file, [lines[0], "{not json", lines[1]])
    assert audit.verify_chain()["reason"] == "line is not valid JSON"


def test_older_unchained_log_is_counted_as_legacy(log_file):
    legacy = [json.dumps({"timestamp": 1.0, "event_type": "old", "details": {}}) for _ in range(2)]
    _save(log_file, legacy)
    _write(2)
    result = audit.verify_chain()
    assert result["ok"] is True
    assert (result["legacy"], result["verified"]) == (2, 2)


def test_unchained_event_slipped_in_later_is_detected(log_file):
    _write(1)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": 2.0, "event_type": "sneaky", "details": {}}) + "\n")
    _write(1)
    result = audit.verify_chain()
    assert result["ok"] is False and result["broken_at"] == 2


def test_concurrent_writers_keep_one_chain(log_file):
    threads = [threading.Thread(target=audit.log_event, args=("vote_cast", {"t": i})) for i in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    result = audit.verify_chain()
    assert result["ok"] is True and result["verified"] == 25


def test_read_events_still_works(log_file):
    _write(2)
    events = audit.read_events()
    assert [e["details"]["i"] for e in events] == [1, 0]  # newest first


def test_verify_endpoint(log_file, monkeypatch):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    _write(2)
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")

    body = client.get("/api/audit/verify").json()
    assert body["ok"] is True and body["verified"] == 2

    lines = _lines(log_file)
    _save(log_file, lines[1:])
    body = client.get("/api/audit/verify").json()
    assert body["ok"] is False and body["broken_at"] == 1
