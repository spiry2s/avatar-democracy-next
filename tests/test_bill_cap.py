"""Per-bill input cap. No network and no live model call.

Pasted text, an upload, and an official URL all become a string before
`summarizer.analyze_bill`. That function is the only place the cap runs, so
the comparison pass and a schema retry see the same excerpt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import comparison, config, llm, summarizer
from backend.grounding import BILL_DATA_BEGIN, BILL_DATA_END, TRUNCATION_MARKER
from main import app, invite_admission_cost

ROOT = Path(__file__).resolve().parents[1]

TAIL = "UNIQUE_TAIL_NOT_SENT The later section changes the imported grain tax."
ANALYSIS = {
    "title": "Plain title",
    "plain_summary": "It does a thing.",
    "plain_summary_quote": TAIL,
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


class FakeProvider(llm.LLMProvider):
    provider_id = "fake"

    def __init__(self, responses: list[Any], *, documents: bool = False):
        self.model = "fake-model"
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)
        self._documents = documents

    def accepts_data_blocks(self) -> bool:
        return self._documents

    def complete(
        self,
        *,
        user: str,
        system: str | None = None,
        max_tokens: int | None = None,
        data_blocks: Any = None,
    ) -> str:
        text = ""
        if data_blocks:
            text = data_blocks[0].text
        self.calls.append(
            {
                "user": user,
                "system": system,
                "max_tokens": max_tokens,
                "data": text,
            }
        )
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _json(payload: Any) -> str:
    return json.dumps(payload)


def _bill_region(user: str) -> str:
    return user.split(BILL_DATA_BEGIN, 1)[1].split(BILL_DATA_END, 1)[0]


def _sent(call: dict[str, Any]) -> str:
    return call["data"] or _bill_region(call["user"])


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_under_the_cap_is_unchanged(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "60000")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    fake = FakeProvider([_json(ANALYSIS)])
    text = "A bill about farms. " * 5
    result = summarizer.analyze_bill(text, provider=fake)
    assert len(fake.calls) == 1
    assert text in fake.calls[0]["user"]
    assert "[EXCERPT]" not in fake.calls[0]["user"]
    assert TRUNCATION_MARKER not in fake.calls[0]["user"]
    assert fake.calls[0]["max_tokens"] == config.LLM_MAX_TOKENS
    assert result.truncation["truncated"] is False
    assert result.truncation["original_chars"] == len(text)
    assert result.truncation["analyzed_chars"] == len(text)


def test_over_the_cap_truncates_with_metadata(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "80")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    bill = "A" * 200
    fake = FakeProvider([_json(ANALYSIS)])
    result = summarizer.analyze_bill(bill, provider=fake)
    sent = _sent(fake.calls[0])
    assert len(fake.calls) == 1
    assert fake.calls[0]["max_tokens"] == config.LLM_MAX_TOKENS
    assert "A" * 81 not in sent
    assert "A" * 80 in sent
    assert "[EXCERPT]" in sent
    assert TRUNCATION_MARKER.strip() in sent
    assert result.truncation["truncated"] is True
    assert result.truncation["original_chars"] == 200
    assert result.truncation["analyzed_chars"] == 80
    assert result.truncation["blocking"] is False
    assert result.truncation["message"] == (
        "This bill is very long; only the first 40% was analyzed."
    )


def test_section_boundary_beats_a_later_paragraph(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "4000")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    alpha = "SEC. 101. Alpha.\n\n" + ("Alpha sentence about farms. " * 30)
    beta = (
        "\n\nSEC. 102. Beta.\n\n"
        + ("Beta sentence that is past the heading. " * 10)
        + "\n\n"
        + ("Closer paragraph that a paragraph cut would keep. " * 4)
    )
    bill = alpha + beta + TAIL
    sec102 = bill.index("SEC. 102")
    last_para = bill.rfind("\n\n")
    assert sec102 < last_para < len(bill)
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", str(last_para + 20))
    assert last_para + 20 < len(bill)
    assert last_para + 20 - sec102 < summarizer._EXCERPT_LOOKBACK

    fake = FakeProvider([_json(ANALYSIS)])
    result = summarizer.analyze_bill(bill, provider=fake)
    sent = _sent(fake.calls[0])
    assert "Alpha sentence about farms." in sent
    assert "Beta sentence" not in sent
    assert "Closer paragraph" not in sent
    assert TAIL not in sent
    assert result.truncation["truncated"] is True
    assert result.truncation["analyzed_chars"] == sec102
    assert result.truncation["original_chars"] == len(bill)
    assert result.sections[0]["grounded"] is False
    assert result.plain_summary_grounding_reason == "quote_outside_analyzed_text"


def test_paragraph_boundary_when_there_is_no_nearby_heading(monkeypatch):
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    first = "Opening paragraph about water rights stays. " * 5
    second = "Later paragraph about tax cuts should drop. " * 5
    bill = first + "\n\n" + second
    break_at = bill.index("\n\n")
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", str(break_at + 40))
    fake = FakeProvider([_json(ANALYSIS)])
    result = summarizer.analyze_bill(bill, provider=fake)
    sent = _sent(fake.calls[0])
    assert "water rights" in sent
    assert "tax cuts" not in sent
    assert result.truncation["analyzed_chars"] == break_at
    assert result.truncation["truncated"] is True


def test_document_block_provider_receives_the_excerpt(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "90")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    bill = "SEC. 101. Keep the farm report.\n\n" + ("Middle. " * 40) + TAIL
    fake = FakeProvider([_json(ANALYSIS)], documents=True)
    summarizer.analyze_bill(bill, provider=fake)
    call = fake.calls[0]
    assert TAIL not in call["data"]
    assert TAIL not in call["user"]
    assert "farm report" in call["data"]
    assert "[EXCERPT]" in call["data"]
    assert call["max_tokens"] == config.LLM_MAX_TOKENS


def test_reject_mode_does_not_call_the_model(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "40")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "reject")
    fake = FakeProvider([_json(ANALYSIS)])
    bill = "UNIQUE_BILL_BODY " * 10
    with pytest.raises(summarizer.BillOversizeError) as exc:
        summarizer.analyze_bill(bill, provider=fake)
    assert fake.calls == []
    assert exc.value.original_chars == len(bill)
    assert exc.value.limit == 40
    assert "UNIQUE_BILL_BODY" not in str(exc.value)
    assert "not sent for analysis" in str(exc.value)


def test_reject_mode_returns_413(client, monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "50")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "reject")
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")

    def factory(role: str = "primary") -> llm.LLMProvider:
        raise AssertionError(f"provider built for {role}")

    monkeypatch.setattr(llm, "get_provider", factory)
    body = "UNIQUE_BILL_BODY " + ("Section 1. " * 20)
    response = client.post("/api/analyze", files={"text": (None, body)})
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert "not sent for analysis" in detail
    assert "50" in detail
    assert str(len(body)) in detail
    assert "UNIQUE_BILL_BODY" not in detail


def test_env_override_changes_the_cap_and_invalid_values_keep_defaults(monkeypatch):
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    text = "B" * 100

    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "40")
    small = summarizer.analyze_bill(text, provider=FakeProvider([_json(ANALYSIS)]))
    assert small.truncation["truncated"] is True
    assert small.truncation["analyzed_chars"] == 40
    assert small.truncation["original_chars"] == 100

    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "500")
    wide = FakeProvider([_json(ANALYSIS)])
    kept = summarizer.analyze_bill(text, provider=wide)
    assert kept.truncation["truncated"] is False
    assert "B" * 100 in wide.calls[0]["user"]
    assert "[EXCERPT]" not in wide.calls[0]["user"]

    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "0")
    assert config.max_bill_chars() == config.DEFAULT_MAX_BILL_CHARS
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "nope")
    assert config.max_bill_chars() == config.DEFAULT_MAX_BILL_CHARS
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "nope")
    assert config.oversize_mode() == "truncate"
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "REJECT")
    assert config.oversize_mode() == "reject"

    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "30")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "TRUNCATE")
    again = summarizer.analyze_bill(text, provider=FakeProvider([_json(ANALYSIS)]))
    assert again.truncation["truncated"] is True
    assert again.truncation["analyzed_chars"] == 30


def test_schema_retry_and_comparison_use_the_same_capped_text(monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "160")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    monkeypatch.setattr(config, "LLM_MAX_TOKENS", 32111)
    monkeypatch.setattr(config, "COMPARISON_MAX_TOKENS", 32222)
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    monkeypatch.setattr(config, "COMPARISON_VOTE_POLICY", "abstain")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")

    head = "SEC. 101. The agency shall publish the farm report each year.\n\n"
    bill = head + ("Middle sentence about water. " * 20) + "\n\n" + TAIL
    assert len(bill) > 160
    assert bill.index(TAIL) > 160

    primary = FakeProvider(["not json", _json(ANALYSIS)])
    other = FakeProvider(["not json", _json(ANALYSIS)],)
    other.provider_id = "openai"
    other.model = "llama3.1:8b"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    result = comparison.analyze_for_api(bill)

    assert len(primary.calls) == 2
    assert len(other.calls) == 2
    regions = [_bill_region(call["user"]) for call in primary.calls + other.calls]
    assert len(set(regions)) == 1
    sent = regions[0]
    assert "farm report" in sent
    assert TAIL not in sent
    assert "[EXCERPT]" in sent
    assert TRUNCATION_MARKER.strip() in sent
    assert "previous response was rejected" in primary.calls[1]["user"]
    assert "previous response was rejected" in other.calls[1]["user"]
    assert "previous response was rejected" not in _bill_region(primary.calls[1]["user"])
    assert primary.calls[0]["max_tokens"] == primary.calls[1]["max_tokens"] == 32111
    assert other.calls[0]["max_tokens"] == other.calls[1]["max_tokens"] == 32222
    assert result["truncation"]["truncated"] is True
    assert result["truncation"]["original_chars"] == len(bill)
    assert result["truncation"]["analyzed_chars"] <= 160
    assert result["truncation"]["analyzed_chars"] < len(bill)


def test_analyze_endpoint_reports_the_excerpt(client, monkeypatch):
    monkeypatch.setenv("AVATAR_MAX_BILL_CHARS", "80")
    monkeypatch.setenv("AVATAR_OVERSIZE_MODE", "truncate")
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    monkeypatch.setattr(
        llm,
        "get_provider",
        lambda role="primary": FakeProvider([_json(ANALYSIS)]),
    )
    bill = "C" * 200
    response = client.post("/api/analyze", files={"text": (None, bill)})
    assert response.status_code == 200
    body = response.json()
    assert body["source_chars"] == 200
    truncation = body["analysis"]["truncation"]
    assert truncation["truncated"] is True
    assert truncation["original_chars"] == 200
    assert truncation["analyzed_chars"] == 80
    assert truncation["message"] == "This bill is very long; only the first 40% was analyzed."


def test_invite_admission_cost_is_unchanged(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    assert invite_admission_cost("POST", "/api/analyze") == (1, 1)
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    assert invite_admission_cost("POST", "/api/analyze") == (1, 2)
    assert invite_admission_cost("POST", "/api/avatar/vote") == (0, 1)


def test_ui_shows_the_oversize_note():
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    vote_js = (ROOT / "frontend" / "vote.js").read_text(encoding="utf-8")
    note = "This bill is very long; only the first"
    assert "t.truncated" in app_js
    assert note in app_js
    assert "grounding-banner warning" in app_js
    assert note in vote_js
    assert "excerptNote" in vote_js
