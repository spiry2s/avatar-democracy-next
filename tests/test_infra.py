"""Tests for the LLM wrapper, section-id matching, storage, audit chain, and URL guard."""

from __future__ import annotations

import json
import socket
from types import SimpleNamespace

import pytest

from backend import audit, chamber, extractors, llm, storage
from backend.avatar import _delegate_followed_sections
from backend.profile import CitizenProfile


# ── llm.call_json ─────────────────────────────────────────────────────────────


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, text="{}", stop_reason="end_turn"):
        self.kwargs = None
        self._msg = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        )
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        return _Stream(self._msg)


SCHEMA = llm.obj({"a": llm.INT})


def test_call_json_parses_and_sends_schema():
    c = FakeClient('{"a": 1}')
    out = llm.call_json(name="t", model="claude-opus-5-5", prompt="p", schema=SCHEMA, effort="high", client=c)
    assert out == {"a": 1}
    assert c.kwargs["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "high"}
    assert "thinking" not in c.kwargs  # adaptive by default on current models


def test_call_json_older_model_gets_explicit_adaptive_thinking():
    c = FakeClient('{"a": 1}')
    llm.call_json(name="t", model="claude-opus-4-8", prompt="p", schema=SCHEMA, client=c)
    assert c.kwargs["thinking"] == {"type": "adaptive"}


def test_call_json_haiku_45_gets_no_thinking_or_effort():
    c = FakeClient('{"a": 1}')
    llm.call_json(name="t", model="claude-haiku-4-5", prompt="p", schema=SCHEMA, effort="high", client=c)
    assert "thinking" not in c.kwargs and "effort" not in c.kwargs["output_config"]


def test_call_json_truncated_raises():
    with pytest.raises(llm.TruncatedOutput):
        llm.call_json(name="t", model="m", prompt="p", schema=SCHEMA,
                      client=FakeClient('{"a":', stop_reason="max_tokens"))


def test_call_json_refusal_raises():
    with pytest.raises(llm.Refused):
        llm.call_json(name="t", model="m", prompt="p", schema=SCHEMA, client=FakeClient("", "refusal"))


# ── chamber section-id matching ───────────────────────────────────────────────


def test_aggregate_matches_reformatted_section_ids():
    analysis = {"sections": [{"section_id": "Sec. 101", "heading": "H"}]}
    tags = {"101": {"liberty": 0.9}}  # tagger dropped the "Sec." prefix
    operator = {"section_votes": [{"section_id": "sec_101", "position": "no"}]}
    pop = [{"archetype": "a", "compass": {"liberty": 0.9}}] * 3
    (t,) = chamber.aggregate(analysis, operator, pop, tags, tau=0.12)
    assert t["tagged"]
    assert (t["yes"], t["no"], t["abstain"]) == (3, 1, 0)


def test_missing_tags_detected():
    analysis = {"sections": [{"section_id": "Sec. 1"}, {"section_id": "Sec. 2"}]}
    assert chamber.missing_tags(analysis, {"1": {}}) == ["Sec. 2"]


def test_drift_matches_name_with_honorific():
    p = CitizenProfile()
    p.add_delegate("climate", "James Hansen")
    p.delegates[0].active_at = 0.0
    p.add_source("climate", "2019", "Supports a carbon tax.")
    out = _delegate_followed_sections(p, [{"section_id": "S1", "basis": "delegate:Dr. James Hansen", "position": "no"}])
    assert len(out) == 1 and out[0]["delegate"] == "James Hansen"


# ── storage ───────────────────────────────────────────────────────────────────


def test_write_json_is_atomic_and_readable(tmp_path):
    p = tmp_path / "x.json"
    storage.write_json(p, {"a": 1})
    assert storage.read_json(p) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]  # no temp files left


def test_read_json_missing_vs_corrupt(tmp_path):
    assert storage.read_json(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(storage.CorruptStateError):
        storage.read_json(bad)


# ── audit hash chain ──────────────────────────────────────────────────────────


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(audit, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", path)
    return path


def test_audit_chain_verifies(audit_file):
    for i in range(3):
        audit.log_event("e", {"i": i})
    v = audit.verify_chain()
    assert v["ok"] and v["verified"] == 3


def test_audit_tampering_detected(audit_file):
    for i in range(3):
        audit.log_event("e", {"i": i})
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[1])
    e["details"]["i"] = 99
    lines[1] = json.dumps(e)
    audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = audit.verify_chain()
    assert not v["ok"] and v["broken_at"] == 1


def test_audit_deletion_detected(audit_file):
    for i in range(3):
        audit.log_event("e", {"i": i})
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    audit_file.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert audit.verify_chain()["broken_at"] == 1


def test_audit_legacy_prefix_tolerated(audit_file):
    audit_file.write_text(json.dumps({"timestamp": 1, "event_type": "old", "details": {}}) + "\n", encoding="utf-8")
    audit.log_event("new", {})
    v = audit.verify_chain()
    assert v["ok"] and v["legacy"] == 1 and v["verified"] == 1


# ── URL guard ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/bill.pdf",
    "http://127.0.0.1:8000/api/profile",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
])
def test_url_guard_rejects_non_public(url):
    with pytest.raises(extractors.SourceError):
        extractors._check_public_url(url)


def test_url_guard_rejects_hostname_resolving_private(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("10.0.0.5", 80))])
    with pytest.raises(extractors.SourceError):
        extractors._check_public_url("http://intranet.example.com/")


def test_url_guard_allows_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("93.184.215.14", 443))])
    extractors._check_public_url("https://www.congress.gov/bill.pdf")
