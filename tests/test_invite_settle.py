"""Issues #5 and #6: an invitee is charged for the model calls a request made.

#5: a vote was charged one call although it made several, so the budget
     allowed far more spending than it said.
#6: a request that failed before any model call still used up credit.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from backend import audit, bills as bills_mod, invites, llm, profile as profile_mod

LONG_TEXT = "Section 1. " + "Bill text. " * 20


@pytest.fixture(autouse=True)
def invite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")
    monkeypatch.delenv("APP_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setattr(profile_mod, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(bills_mod, "BILLS_DIR", tmp_path / "bills")
    main.reset_rate_limits()
    invites.close_store()
    yield
    invites.close_store()


def _tester(**budget) -> tuple[TestClient, str]:
    issued = invites.create_invite(label="ada", **budget)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert http.get("/", params={"token": issued.token}, follow_redirects=False).status_code == 303
    return http, issued.invite.id


def _analysis_that_calls(monkeypatch, calls: int, *, then_fail: Exception | None = None) -> None:
    def fake(text):
        for _ in range(calls):
            llm._note_request_usage(llm.TokenUsage(input_tokens=10, output_tokens=5))
        if then_fail is not None:
            raise then_fail
        return {"title": "t", "sections": []}

    monkeypatch.setattr(main.comparison, "analyze_for_api", fake)


def _row(invite_id: str) -> invites.Invite:
    row = invites.get_invite(invite_id)
    assert row is not None
    return row


# ── #5: charged what was actually used ───────────────────────────────────────


def test_a_request_making_three_calls_is_charged_three(monkeypatch):
    _analysis_that_calls(monkeypatch, 3)
    http, invite_id = _tester(bills=5, calls=10)

    assert http.post("/api/analyze", files={"text": (None, LONG_TEXT)}).status_code == 200

    row = _row(invite_id)
    assert (row.bills_used, row.llm_calls_used) == (1, 3)


def test_overspend_is_recorded_and_the_next_request_is_refused(monkeypatch):
    _analysis_that_calls(monkeypatch, 3)
    http, invite_id = _tester(bills=5, calls=2)

    assert http.post("/api/analyze", files={"text": (None, LONG_TEXT)}).status_code == 200
    assert _row(invite_id).llm_calls_used == 3  # the calls happened; they are not hidden

    refused = http.post("/api/analyze", files={"text": (None, LONG_TEXT)})
    assert refused.status_code == 429
    assert refused.json()["detail"] == "Invite model-call budget exhausted"


# ── #6: no model call, no charge ─────────────────────────────────────────────


def test_too_short_text_is_not_charged():
    http, invite_id = _tester(bills=1, calls=1)

    for _ in range(3):
        assert http.post("/api/analyze", files={"text": (None, "hi")}).status_code == 400

    row = _row(invite_id)
    assert (row.bills_used, row.bills_used_today, row.llm_calls_used) == (0, 0, 0)


def test_missing_bill_routes_are_not_charged():
    http, invite_id = _tester(bills=1, calls=1)

    assert http.post("/api/bills/bill-0000/vote").status_code == 404
    assert http.post("/api/bills/bill-0000/endorse").status_code == 404

    assert _row(invite_id).llm_calls_used == 0


def test_endorsement_that_triggers_no_analysis_is_not_charged():
    http, invite_id = _tester(bills=1, calls=1)
    bill_id = http.post("/api/bills", json={"title": "B", "text": LONG_TEXT}).json()["bill"]["id"]
    assert http.post(f"/api/bills/{bill_id}/open").status_code == 200

    assert http.post(f"/api/bills/{bill_id}/endorse").status_code == 200  # below threshold: no freeze

    assert _row(invite_id).llm_calls_used == 0


def test_failed_analysis_after_model_calls_keeps_the_charge(monkeypatch):
    _analysis_that_calls(monkeypatch, 2, then_fail=ValueError("Could not parse response as JSON"))
    http, invite_id = _tester(bills=5, calls=10)

    assert http.post("/api/analyze", files={"text": (None, LONG_TEXT)}).status_code == 502

    row = _row(invite_id)
    # The model was paid for, so the calls and the bill stay charged.
    assert (row.bills_used, row.llm_calls_used) == (1, 2)


def test_crash_before_any_model_call_is_refunded(monkeypatch):
    def explode(text):
        raise RuntimeError("provider not configured")

    monkeypatch.setattr(main.comparison, "analyze_for_api", explode)
    http, invite_id = _tester(bills=5, calls=10)

    assert http.post("/api/analyze", files={"text": (None, LONG_TEXT)}).status_code == 500

    row = _row(invite_id)
    assert (row.bills_used, row.llm_calls_used) == (0, 0)


# ── the store function ───────────────────────────────────────────────────────


def test_settle_never_goes_below_zero():
    issued = invites.create_invite(label="ada", bills=5, calls=5)
    assert invites.settle(issued.invite.id, reserved_bills=1, reserved_calls=4, used_calls=0, refund_bills=True)
    row = _row(issued.invite.id)
    assert (row.bills_used, row.bills_used_today, row.llm_calls_used) == (0, 0, 0)


def test_settle_unknown_invite_and_bad_counts():
    assert invites.settle("0" * 16, reserved_bills=0, reserved_calls=1, used_calls=0, refund_bills=False) is False
    with pytest.raises(ValueError):
        invites.settle("0" * 16, reserved_bills=-1, reserved_calls=0, used_calls=0, refund_bills=False)


# ── comparison passes run on worker threads ──────────────────────────────────


def test_comparison_worker_calls_count_even_when_a_pass_fails(monkeypatch):
    from backend import comparison, config, summarizer

    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "other-vendor-model")
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")

    def fake_analyze(text, **kwargs):
        llm._note_request_usage(llm.TokenUsage(input_tokens=1, output_tokens=1))
        raise ValueError("Could not parse response as JSON")

    monkeypatch.setattr(summarizer, "analyze_bill", fake_analyze)

    token = llm.begin_request_usage()
    try:
        with pytest.raises(ValueError):
            comparison.analyze_for_api(LONG_TEXT)
        calls, _tokens = llm.snapshot_request_usage()
    finally:
        llm.end_request_usage(token)
    assert calls == 2  # both passes called the model before failing
