"""Vote on a finished analysis, and paste a bill link on the Vote page.

No network and no provider calls. A fake provider stands in for the model.
The link field uses POST /api/analyze, the same allowlisted fetch as Analyze.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from backend import signing, avatar, bill_fetch, comparison, config, extractors, invites, llm, summarizer
from backend.profile import CitizenProfile
from backend.vote_analysis import (
    VOTE_ANALYSIS_PROMPT_FACTOR,
    VOTE_ANALYSIS_RAW_FACTOR,
    AnalysisRejected,
    accept_vote_analysis,
    vote_analysis_too_large,
    vote_prompt_chars,
)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class FakeProvider(llm.LLMProvider):
    provider_id = "fake"

    def __init__(self, responses: list[Any]):
        self.model = "fake-model"
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)

    def complete(
        self,
        *,
        user: str,
        system: str | None = None,
        max_tokens: int = 8000,
        data_blocks: Any = None,
    ) -> str:
        self.calls.append({"user": user, "system": system, "max_tokens": max_tokens})
        # Report the call like the real providers, so invite budgets see it.
        self.note_usage(llm.TokenUsage())
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _analysis(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": "Grain tax",
        "plain_summary": "It taxes imported grain.",
        "sections": [
            {
                "section_id": "s1",
                "heading": "The tax",
                "summary": "A tax on imported grain.",
                "significance": "high",
            }
        ],
        "beneficiaries": {"benefits": [], "costs": [], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {},
    }
    data.update(overrides)
    return data


def _signed(analysis: dict[str, Any]) -> dict[str, Any]:
    """Sign a copy the way /api/analyze signs what it returns."""
    return signing.sign(json.loads(json.dumps(analysis)))


def _bare() -> dict[str, Any]:
    """An analysis whose prompt text is empty until a test fills it in."""
    return {
        "title": "",
        "plain_summary": "",
        "sections": [{"section_id": "", "heading": "", "summary": ""}],
        "beneficiaries": {"benefits": [], "costs": [], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {},
    }


def _vote(position: str = "no") -> str:
    return json.dumps({
        "section_votes": [
            {
                "section_id": "s1",
                "heading": "The tax",
                "position": position,
                "basis": "values",
                "reasoning": "The tax is the part that matters.",
                "confidence": "high",
            }
        ],
        "recommendation": "Vote no on the tax.",
        "would_pass_sections": [],
        "key_tension": "",
    })


def _profile() -> CitizenProfile:
    return CitizenProfile(values="Keep taxes low.")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "bind_host", "127.0.0.1")
    monkeypatch.setattr(main, "bind_port", 8000)
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    monkeypatch.setattr(main.audit, "log_event", lambda *args, **kwargs: {})
    main.reset_rate_limits()
    return TestClient(main.app, base_url="http://127.0.0.1:8000")


def _no_analyze(_text: str) -> dict[str, Any]:
    raise AssertionError("analyze ran")


def _arm_vote(monkeypatch, responses: list[Any] | None = None) -> FakeProvider:
    fake = FakeProvider(responses if responses is not None else [_vote()])
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    monkeypatch.setattr(llm, "get_provider", lambda role="primary": fake)
    monkeypatch.setattr(main.profile_mod, "load_profile", lambda: _profile())
    monkeypatch.setattr(comparison, "analyze_for_api", _no_analyze)
    return fake


def _forbid_dns(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("dns")

    monkeypatch.setattr(bill_fetch.socket, "getaddrinfo", explode)


# ── schema and the bill cap ───────────────────────────────────────────────────


def test_prompt_budget_is_twice_the_bill_cap_and_one_field_cannot_exceed_it(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "80")
    limit = config.max_bill_chars()
    assert limit == 80
    fitting = _bare()
    fitting["title"] = "a" * limit
    fitting["plain_summary"] = "b" * limit
    cleaned = accept_vote_analysis(fitting)
    assert vote_prompt_chars(cleaned) == limit * VOTE_ANALYSIS_PROMPT_FACTOR
    assert "smuggled" not in cleaned

    over = _bare()
    over["title"] = "a" * limit
    over["plain_summary"] = "b" * limit
    over["sections"][0]["summary"] = "c"
    with pytest.raises(AnalysisRejected) as prompt_cap:
        accept_vote_analysis(over)
    assert prompt_cap.value.status_code == 413
    assert prompt_cap.value.detail == vote_analysis_too_large(limit)

    one_field = _bare()
    one_field["title"] = "d" * (limit + 1)
    with pytest.raises(AnalysisRejected) as field_cap:
        accept_vote_analysis(one_field)
    assert field_cap.value.status_code == 413
    assert "d" * 10 not in field_cap.value.detail


def test_raw_tree_cap_counts_text_the_prompt_would_not_send(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "40")
    limit = 40
    fitting = _bare()
    fitting["comparable_laws"] = ["e" * limit]
    assert accept_vote_analysis(fitting)["comparable_laws"] == ["e" * limit]
    assert vote_prompt_chars(accept_vote_analysis(fitting)) == 0

    over = _bare()
    over["comparable_laws"] = ["e" * limit for _ in range(VOTE_ANALYSIS_RAW_FACTOR + 1)]
    with pytest.raises(AnalysisRejected) as exc:
        accept_vote_analysis(over)
    assert exc.value.status_code == 413
    assert "e" * 8 not in exc.value.detail


def test_schema_miss_names_the_field_and_drops_unknown_properties():
    with pytest.raises(ValueError, match="missing title"):
        accept_vote_analysis({})
    forged = _analysis(ignore_previous_instructions="SMUGGLED_ORDER", grounded_override=True)
    cleaned = accept_vote_analysis(forged)
    assert "ignore_previous_instructions" not in cleaned
    assert "SMUGGLED_ORDER" not in json.dumps(cleaned)
    assert cleaned["title"] == "Grain tax"


def test_grounding_marks_survive_and_ungrounded_text_is_not_prompt_text():
    data = _analysis(
        sections=[
            {
                "section_id": "s1",
                "heading": "Kept",
                "summary": "GROUNDED_SUMMARY",
                "grounded": True,
                "grounding_reason": "exact",
            },
            {
                "section_id": "s2",
                "heading": "Hidden heading",
                "summary": "UNGROUNDED_SUMMARY",
                "grounded": False,
                "grounding_reason": "missing_quote",
            },
        ],
    )
    cleaned = accept_vote_analysis(data)
    assert cleaned["sections"][1]["grounded"] is False
    assert cleaned["sections"][1]["summary"] == "UNGROUNDED_SUMMARY"
    withheld = vote_prompt_chars(cleaned)
    shown = [dict(section) for section in cleaned["sections"]]
    shown[1]["grounded"] = True
    extra = len("Hidden heading") + len("UNGROUNDED_SUMMARY")
    assert vote_prompt_chars({**cleaned, "sections": shown}) == withheld + extra


def test_analyzer_output_round_trips_into_a_vote_analysis(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    model = {
        "title": "Plain title",
        "plain_summary": "It does a thing.",
        "sections": [
            {
                "section_id": "s1",
                "heading": "A heading",
                "summary": "Section summary.",
                "significance": "high",
                "significance_reason": "Matters.",
            }
        ],
        "beneficiaries": {"benefits": ["farmers"], "costs": ["taxpayers"], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {"complexity": "low"},
    }
    result = summarizer.analyze_bill("Section 1. A grain tax.", provider=FakeProvider([json.dumps(model)]))
    cleaned = accept_vote_analysis(result.to_dict())
    assert cleaned["title"] == result.title
    assert cleaned["sections"][0]["grounded"] is result.sections[0]["grounded"]
    assert "token_usage" not in cleaned
    assert isinstance(cleaned.get("truncation"), dict)
    assert isinstance(cleaned.get("beneficiary_claims"), list)


def test_depth_and_list_bombs_never_reach_schema_validation(monkeypatch):
    called = {"schema": False}

    def boom(_data):
        called["schema"] = True
        raise AssertionError("schema ran")

    monkeypatch.setattr("backend.vote_analysis.validate_analysis", boom)
    deep: Any = "x"
    for _ in range(30):
        deep = {"k": deep}
    with pytest.raises(ValueError, match="too deep"):
        accept_vote_analysis(_analysis(smuggled=deep))
    wide = _analysis()
    wide["comparable_laws"] = ["x"] * 2001
    with pytest.raises(AnalysisRejected) as exc:
        accept_vote_analysis(wide)
    assert exc.value.status_code == 413
    assert called["schema"] is False


# ── endpoint ──────────────────────────────────────────────────────────────────


def test_vote_uses_the_posted_analysis_and_does_not_analyze(client, monkeypatch):
    fake = _arm_vote(monkeypatch)
    first = _analysis(title="Bill Alpha", plain_summary="ALPHA_MARKER is the summary.")
    other = _analysis(title="Bill Beta", plain_summary="BETA_MARKER is the other bill.")
    other["ignore_previous_instructions"] = "SMUGGLED_ORDER"
    response = client.post("/api/avatar/vote", json={"analysis": _signed(other)})
    assert response.status_code == 200, response.text
    body = response.json()["vote"]
    assert body["bill_title"] == "Bill Beta"
    assert len(fake.calls) == 1
    prompt = fake.calls[0]["user"]
    assert "BETA_MARKER" in prompt
    assert "ALPHA_MARKER" not in prompt
    assert "SMUGGLED_ORDER" not in prompt
    assert first["title"] not in prompt


def test_ungrounded_summary_is_withheld_from_the_vote_prompt(client, monkeypatch):
    fake = _arm_vote(monkeypatch)
    data = _analysis(
        sections=[
            {
                "section_id": "s1",
                "heading": "Kept",
                "summary": "GROUNDED_SUMMARY",
                "grounded": True,
            },
            {
                "section_id": "s2",
                "heading": "Hidden heading",
                "summary": "UNGROUNDED_SUMMARY",
                "grounded": False,
                "grounding_reason": "missing_quote",
            },
        ],
    )
    response = client.post("/api/avatar/vote", json={"analysis": _signed(data)})
    assert response.status_code == 200, response.text
    prompt = fake.calls[0]["user"]
    assert "GROUNDED_SUMMARY" in prompt
    assert "UNGROUNDED_SUMMARY" not in prompt
    assert "Hidden heading" not in prompt


def test_comparison_disagreement_still_abstains_after_the_cap(client, monkeypatch):
    fake = _arm_vote(monkeypatch, [_vote("yes")])
    data = _analysis(
        model_comparison={
            "enabled": True,
            "status": "available",
            "policy": "abstain",
            "high_severity_disagreements": [
                {
                    "section": "s1",
                    "type": "exemption",
                    "kind": "content",
                    "raised_by": "comparison",
                    "description": "COMPARISON_NOVEL should not be sent to the model.",
                }
            ],
        },
    )
    response = client.post("/api/avatar/vote", json={"analysis": _signed(data)})
    assert response.status_code == 200, response.text
    vote = response.json()["vote"]["section_votes"][0]
    assert vote["position"] == "abstain"
    assert vote["comparison_abstained"] is True
    assert "COMPARISON_NOVEL" not in fake.calls[0]["user"]


def test_oversized_and_garbage_analyses_do_not_call_the_model(client, monkeypatch):
    fake = _arm_vote(monkeypatch)

    def explode(*_args, **_kwargs):
        raise AssertionError("cast_vote ran")

    monkeypatch.setattr(main.avatar, "cast_vote", explode)
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "50")
    huge = _analysis()
    huge["plain_summary"] = "Z" * 51
    rejected = client.post("/api/avatar/vote", json={"analysis": _signed(huge)})
    assert rejected.status_code == 413
    assert rejected.json()["detail"] == vote_analysis_too_large(50)
    assert "Z" * 10 not in rejected.text

    garbage = client.post("/api/avatar/vote", json={"analysis": _signed({"title": "Only a title"})})
    assert garbage.status_code == 400
    assert "failed schema" in garbage.json()["detail"]
    assert fake.calls == []


def test_blocking_truncation_does_not_call_the_model(client, monkeypatch):
    fake = _arm_vote(monkeypatch, [])
    data = _analysis(
        truncation={
            "truncated": True,
            "blocking": True,
            "message": "This bill is very long; only the first 40% was analyzed.",
            "original_chars": 200,
            "analyzed_chars": 80,
        },
    )
    response = client.post("/api/avatar/vote", json={"analysis": _signed(data)})
    assert response.status_code == 200, response.text
    vote = response.json()["vote"]
    assert vote["truncation_blocking"] is True
    assert "only the first 40%" in vote["blocking_reason"]
    assert fake.calls == []


def test_vote_does_not_reserve_a_bill_credit(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_INVITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AVATAR_INVITE_MODE", "1")
    invites.close_store()
    fake = _arm_vote(monkeypatch, [_vote(), _vote()])
    issued = invites.create_invite(label="ada", bills=1, calls=5)
    http = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert http.get("/", params={"token": issued.token}, follow_redirects=False).status_code == 303

    first = http.post("/api/avatar/vote", json={"analysis": _signed(_analysis())})
    assert first.status_code == 200, first.text
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert row.bills_used == 0
    assert row.llm_calls_used == 1

    second = http.post("/api/avatar/vote", json={"analysis": _signed(_analysis(title="Second bill"))})
    assert second.status_code == 200, second.text
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert row.bills_used == 0
    assert row.llm_calls_used == 2
    assert len(fake.calls) == 2
    assert main.invite_admission_cost("POST", "/api/avatar/vote") == (0, 1)

    huge = _analysis()
    huge["title"] = "Q" * (config.max_bill_chars() + 1)
    rejected = http.post("/api/avatar/vote", json={"analysis": _signed(huge)})
    assert rejected.status_code == 413
    row = invites.get_invite(issued.invite.id)
    assert row is not None
    assert row.bills_used == 0
    # Admission reserves a call, and settling refunds it: the handler refused
    # the body before any model call (issue #6).
    assert row.llm_calls_used == 2
    invites.close_store()


def test_vote_link_uses_the_allowlist_and_the_blocked_messages(client, monkeypatch):
    _forbid_dns(monkeypatch)

    def fetched(_url):
        raise AssertionError("fetch ran")

    monkeypatch.setattr(bill_fetch, "fetch_official_document", fetched)
    blocked_host = client.post(
        "/api/analyze",
        files={"url": (None, "https://example.com/bill.pdf")},
    )
    assert blocked_host.status_code == 400
    assert blocked_host.json()["detail"] == bill_fetch.MSG_UNSUPPORTED
    assert blocked_host.json()["code"] == "unsupported"

    cases = (
        ("source_blocked", 422),
        ("redirect_loop", 409),
        ("too_many_redirects", 400),
    )
    for code, status in cases:
        def fail(_url, status=status, code=code):
            raise bill_fetch.BillSourceError(bill_fetch.MSG_BLOCKED, status, code)

        monkeypatch.setattr(extractors, "fetch_official_document", fail)
        response = client.post(
            "/api/analyze",
            files={"url": (None, "https://www.congress.gov/bill/118/house-bill/1")},
        )
        assert response.status_code == status
        assert response.json()["detail"] == bill_fetch.MSG_BLOCKED
        assert response.json()["code"] == code
        assert "cloudflare" not in response.text.lower()


# ── pages ─────────────────────────────────────────────────────────────────────


def _page(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_vote_page_offers_the_same_bill_link_without_inline_script():
    html = _page("vote.html")
    css = _page("style.css")
    js = _page("vote.js")
    lowered = html.lower()
    assert 'id="bill-url"' in html
    assert "Paste a bill link" in html
    assert "Supported sources" in html
    assert "download the file and upload it" in html
    assert "Drop a PDF here, or click to select" in html
    assert "Paste bill text here..." in html
    assert "<script>" not in lowered
    assert "onclick=" not in lowered
    assert "onload=" not in lowered
    assert "javascript:" not in lowered
    assert "formData.append('url', url)" in js
    assert "Fetching the bill" in js
    assert "Use either a PDF or a link, not both." in js
    assert "err.detail" in js
    assert "matchMedia" not in js
    assert "apiFetch(" in js
    click = js[js.index("voteBtn.addEventListener"):js.index("function setStatus")]
    assert click.index("if (handoff)") < click.index("new FormData")
    assert "@media (prefers-color-scheme: dark)" in css
    panel = css.split(".link-panel {", 1)[1].split("}", 1)[0]
    assert "#" not in panel
    assert "var(--" in panel


def test_analyze_hands_the_analysis_to_vote_and_vote_can_clear_it():
    app_js = _page("app.js")
    vote_js = _page("vote.js")
    api_js = _page("api.js")
    analyze = _page("analyze.html")
    vote = _page("vote.html")
    assert 'id="vote-analysis"' in analyze
    assert "Vote on this analysis" in analyze
    assert "window.VOTE_HANDOFF_KEY" in api_js
    assert "avatarDemocracyVoteAnalysis" in api_js
    assert "JSON.stringify({ v: 1, analysis: lastAnalysis })" in app_js
    assert "window.location.assign('/vote')" in app_js
    assert "window.VOTE_HANDOFF_KEY" in vote_js
    assert "data.v !== 1" in vote_js
    assert "sessionStorage.removeItem(HANDOFF_KEY)" in vote_js
    assert 'id="handoff-clear"' in vote
    assert 'id="handoff-bill"' in vote
    assert "label.textContent" in vote_js
    assert "getElementById('handoff-bill').innerHTML" not in vote_js
    vote_fn = vote_js[vote_js.index("async function voteOnAnalysis"):vote_js.index("voteBtn.addEventListener")]
    assert "/api/avatar/vote" in vote_fn
    assert "/api/analyze" not in vote_fn
    assert "FormData" not in vote_fn
    assert "JSON.stringify({ analysis: analysis })" in vote_fn
    assert "only the first" in vote_js
    assert "excerptNote" in vote_js
