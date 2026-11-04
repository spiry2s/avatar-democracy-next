"""
Provider abstraction and every LLM call site.

No network and no API key. A fake provider stands in for the vendor SDK.
The Anthropic implementation is checked against a stand-in client object.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import avatar, chamber, config, conflict, llm, summarizer
from backend.profile import CitizenProfile
from main import app


class FakeProvider(llm.LLMProvider):
    """Scripted provider. Each complete() consumes one response or exception."""

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
    ) -> str:
        self.calls.append({"user": user, "system": system, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


ANALYSIS = {
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

BILL = {
    "title": "Surveillance Bill",
    "plain_summary": "Expands monitoring.",
    "sections": [
        {
            "section_id": "s1",
            "heading": "Warrants",
            "summary": "Allows warrantless search.",
            "significance": "high",
        }
    ],
    "red_flags": [],
    "beneficiaries": {},
}


def _vote(position: str = "no", basis: str = "values", confidence: str = "high") -> dict[str, Any]:
    return {
        "section_votes": [
            {
                "section_id": "s1",
                "heading": "Warrants",
                "position": position,
                "basis": basis,
                "reasoning": "Privacy.",
                "confidence": confidence,
            }
        ],
        "recommendation": "Vote no.",
        "would_pass_sections": [],
        "key_tension": "",
    }


def _profile() -> CitizenProfile:
    return CitizenProfile(values="Protect privacy.", compass={"liberty": 0.4})


def _grounded_profile() -> CitizenProfile:
    profile = CitizenProfile(values="Protect privacy.", compass={"liberty": 0.4})
    profile.add_delegate("surveillance", "ACLU")
    profile.delegates[0].active_at = 0.0
    profile.add_source("surveillance", "2023 statement", "Opposes warrantless surveillance.")
    return profile


def _json(payload: Any) -> str:
    return json.dumps(payload)


# ── provider selection ────────────────────────────────────────────────────────


def test_registered_providers():
    assert list(llm.PROVIDERS) == ["anthropic", "openai"]


def test_provider_id_blank_uses_default():
    assert config._provider_id(None, "anthropic") == "anthropic"
    assert config._provider_id("  ", "anthropic") == "anthropic"
    assert config._provider_id("Anthropic", "anthropic") == "anthropic"
    assert config._provider_id(" OpenAI ", "anthropic") == "openai"


def test_roles_pick_provider_and_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "CHECKER_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "primary-model")
    monkeypatch.setattr(config, "CHECKER_MODEL", "checker-model")

    primary = llm.get_provider("primary")
    checker = llm.get_provider("checker")

    assert isinstance(primary, llm.AnthropicProvider)
    assert isinstance(checker, llm.AnthropicProvider)
    assert primary.model == "primary-model"
    assert checker.model == "checker-model"
    assert primary.provider_id == "anthropic"
    assert checker.provider_id == "anthropic"


def test_checker_provider_can_differ_from_primary(monkeypatch):
    """The registry is the extension point. A second vendor is a factory away."""

    class Stub(llm.LLMProvider):
        provider_id = "stub"

        def __init__(self, model: str):
            self.model = model

        def complete(self, *, user: str, system: str | None = None, max_tokens: int = 8000) -> str:
            return "{}"

    monkeypatch.setitem(llm.PROVIDERS, "stub", lambda model: Stub(model))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "CHECKER_PROVIDER", "stub")
    monkeypatch.setattr(config, "CHECKER_MODEL", "stub-model")

    checker = llm.get_provider("checker")
    primary = llm.get_provider("primary")
    assert checker.provider_id == "stub"
    assert checker.model == "stub-model"
    assert primary.provider_id == "anthropic"


def test_unknown_provider_is_a_clear_config_error(monkeypatch):
    # "openai" is a real provider now. This still checks an unknown id.
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    with pytest.raises(llm.LLMConfigError, match="gemini") as exc:
        llm.get_provider("primary")
    assert isinstance(exc.value, RuntimeError)
    assert "Available providers: anthropic, openai" in str(exc.value)


def test_unknown_role_is_a_clear_config_error():
    with pytest.raises(llm.LLMConfigError, match="primary"):
        llm.get_provider("sidecar")


def test_missing_key_does_not_construct_a_client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")

    def boom(*args, **kwargs):
        raise AssertionError("Anthropic client was constructed")

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    with pytest.raises(llm.LLMConfigError, match="ANTHROPIC_API_KEY") as exc:
        llm.get_provider("primary")
    assert "console.anthropic.com" in str(exc.value)
    assert isinstance(exc.value, RuntimeError)


def test_blank_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    assert llm.credentials_configured() is False
    with pytest.raises(llm.LLMConfigError, match="ANTHROPIC_API_KEY"):
        llm.get_provider("checker")


def test_credentials_configured_follows_provider(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.credentials_configured() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.credentials_configured() is True
    assert llm.credentials_configured("openai") is False


def test_complete_json_strips_fences_and_rejects_garbage():
    fenced = "```json\n" + _json({"a": 1}) + "\n```"
    parsed = FakeProvider([fenced]).complete_json(user="u", system="s", max_tokens=3)
    assert parsed == {"a": 1}

    with pytest.raises(ValueError, match="Could not parse response as JSON"):
        FakeProvider(["not json"]).complete_json(user="u")


def test_anthropic_complete_matches_messages_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    calls: dict[str, Any] = {}

    class FakeMessages:
        def create(self, **kwargs):
            calls["kwargs"] = kwargs
            return SimpleNamespace(
                content=[
                    SimpleNamespace(),  # non-text block, ignored
                    SimpleNamespace(text="part-a"),
                    SimpleNamespace(text=" part-b"),
                ]
            )

    class FakeClient:
        def __init__(self, api_key):
            calls["api_key"] = api_key
            self.messages = FakeMessages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    provider = llm.get_provider("primary")

    assert provider.complete(user="hello", system="sys", max_tokens=12) == "part-a part-b"
    assert calls["api_key"] == "sk-test"
    assert calls["kwargs"] == {
        "model": "claude-sonnet-4-6",
        "max_tokens": 12,
        "system": "sys",
        "messages": [{"role": "user", "content": "hello"}],
    }

    provider.complete(user="no-system", max_tokens=5)
    assert "system" not in calls["kwargs"]
    assert calls["kwargs"]["messages"] == [{"role": "user", "content": "no-system"}]
    assert calls["kwargs"]["max_tokens"] == 5


def test_anthropic_api_errors_propagate(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    class FakeMessages:
        def create(self, **kwargs):
            raise RuntimeError("upstream down")

    class FakeClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    provider = llm.AnthropicProvider(model="m")
    with pytest.raises(RuntimeError, match="upstream down"):
        provider.complete(user="hello", max_tokens=10)


# ── call sites ────────────────────────────────────────────────────────────────


def test_analyze_bill_uses_primary_prompt_and_parses_fenced_json():
    fake = FakeProvider(["```json\n" + _json(ANALYSIS) + "\n```"])
    result = summarizer.analyze_bill("A bill about farms. " * 5, provider=fake)
    assert result.title == "Plain title"
    assert result.sections[0]["section_id"] == "s1"
    assert result.beneficiaries["benefits"] == ["farmers"]
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["system"] == summarizer.SYSTEM_PROMPT
    assert call["user"].startswith(summarizer.ANALYSIS_PROMPT)
    assert "A bill about farms." in call["user"]
    assert call["max_tokens"] == 8000


def test_analyze_bill_empty_does_not_call_provider():
    fake = FakeProvider([])
    with pytest.raises(ValueError, match="empty"):
        summarizer.analyze_bill("   ", provider=fake)
    assert fake.calls == []


def test_analyze_bill_truncates_long_text():
    fake = FakeProvider([_json(ANALYSIS)])
    huge = "A" * (summarizer.MAX_SINGLE_PASS_CHARS + 50)
    summarizer.analyze_bill(huge, provider=fake)
    user = fake.calls[0]["user"]
    assert "[BILL TRUNCATED FOR LENGTH]" in user
    assert "A" * (summarizer.MAX_SINGLE_PASS_CHARS + 1) not in user


def test_analyze_bill_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    with pytest.raises(llm.LLMConfigError, match="ANTHROPIC_API_KEY"):
        summarizer.analyze_bill("some bill text that is not empty")


def test_cast_vote_primary_only_when_checks_disabled(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    fake = FakeProvider([_json(_vote())])
    result = avatar.cast_vote(BILL, _profile(), provider=fake)
    assert result.section_votes[0].position == "no"
    assert result.section_votes[0].divergent is False
    assert result.section_votes[0].delegate_drift is False
    assert len(fake.calls) == 1
    assert fake.calls[0]["system"] == avatar.SYSTEM_PROMPT
    assert "Protect privacy." in fake.calls[0]["user"]
    assert "Surveillance Bill" in fake.calls[0]["user"]
    assert fake.calls[0]["max_tokens"] == 8000


def test_cast_vote_checker_disagreement_and_drift(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", True)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", True)
    primary = FakeProvider([_json(_vote(position="yes", basis="delegate:ACLU"))])
    checker = FakeProvider([_json({"s1": "no"}), _json({"s1": "no"})])
    result = avatar.cast_vote(BILL, _grounded_profile(), provider=primary, checker=checker)
    assert result.section_votes[0].divergent is True
    assert result.section_votes[0].confidence == "low"
    assert result.section_votes[0].delegate_drift is True
    assert len(primary.calls) == 1
    assert len(checker.calls) == 2
    assert checker.calls[0]["max_tokens"] == 2000
    assert checker.calls[0]["system"] == avatar.SYSTEM_PROMPT
    assert checker.calls[1]["max_tokens"] == 1000
    assert checker.calls[1]["system"] is None
    assert "ACLU" in checker.calls[1]["user"]
    assert "2023 statement" in checker.calls[1]["user"]


def test_checker_failure_does_not_break_the_vote(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", True)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", True)
    primary = FakeProvider([_json(_vote(position="no", basis="delegate:ACLU"))])
    checker = FakeProvider([RuntimeError("checker down"), RuntimeError("drift down")])
    result = avatar.cast_vote(BILL, _grounded_profile(), provider=primary, checker=checker)
    assert result.section_votes[0].position == "no"
    assert result.section_votes[0].divergent is False
    assert result.section_votes[0].delegate_drift is False
    assert len(checker.calls) == 2


def test_cast_vote_requests_primary_then_checker(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", True)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    roles: list[str] = []

    def factory(role: str = "primary") -> FakeProvider:
        roles.append(role)
        if role == "primary":
            return FakeProvider([_json(_vote(position="no"))])
        return FakeProvider([_json({"s1": "yes"})])

    monkeypatch.setattr(llm, "get_provider", factory)
    result = avatar.cast_vote(BILL, _profile())
    assert roles == ["primary", "checker"]
    assert result.section_votes[0].divergent is True
    assert result.section_votes[0].confidence == "low"


def test_cross_check_coerces_fenced_objects():
    payload = "```json\n" + _json({"Sec. 1": {"position": "yes", "reasoning": "x"}}) + "\n```"
    fake = FakeProvider([payload])
    out = avatar.cross_check(BILL, _profile(), provider=fake)
    assert out == {"1": "yes"}
    assert fake.calls[0]["max_tokens"] == 2000


def test_detect_drift_flags_contradiction_and_skips_ungrounded():
    fake = FakeProvider([_json({"s1": "no"})])
    flagged = avatar.detect_drift(
        _grounded_profile(),
        BILL,
        [{"section_id": "s1", "basis": "delegate:ACLU", "position": "yes"}],
        provider=fake,
    )
    assert flagged == {"s1"}
    assert fake.calls[0]["system"] is None
    assert fake.calls[0]["max_tokens"] == 1000

    idle = FakeProvider([])
    assert avatar.detect_drift(
        _profile(),
        BILL,
        [{"section_id": "s1", "basis": "values", "position": "yes"}],
        provider=idle,
    ) == set()
    assert idle.calls == []


def test_detect_drift_requests_checker_role(monkeypatch):
    roles: list[str] = []

    def factory(role: str = "primary") -> FakeProvider:
        roles.append(role)
        return FakeProvider([_json({"s1": "yes"})])

    monkeypatch.setattr(llm, "get_provider", factory)
    flagged = avatar.detect_drift(
        _grounded_profile(),
        BILL,
        [{"section_id": "s1", "basis": "delegate:ACLU", "position": "yes"}],
    )
    assert roles == ["checker"]
    assert flagged == set()


def test_tag_sections_clamps_and_uses_primary_prompt():
    raw = {"s1": {"economic": 2.5, "liberty": -0.4}, "s2": "bad"}
    fake = FakeProvider([_json(raw)])
    analysis = {
        "title": "Tagged",
        "sections": [
            {"section_id": "s1", "heading": "H", "summary": "S"},
            {"section_id": "s2", "heading": "H2", "summary": "S2"},
        ],
    }
    tags = chamber.tag_sections(analysis, provider=fake)
    assert tags["s1"]["economic"] == 1.0
    assert tags["s1"]["liberty"] == -0.4
    assert tags["s1"]["social"] == 0.0
    assert tags["s2"]["economic"] == 0.0
    assert fake.calls[0]["system"] is None
    assert fake.calls[0]["max_tokens"] == 2000
    assert "Tagged" in fake.calls[0]["user"]
    assert "[s1]" in fake.calls[0]["user"]


def test_tag_sections_no_sections_skips_provider():
    fake = FakeProvider([])
    assert chamber.tag_sections({"sections": []}, provider=fake) == {}
    assert fake.calls == []


def test_tag_sections_requests_primary_role(monkeypatch):
    roles: list[str] = []

    def factory(role: str = "primary") -> FakeProvider:
        roles.append(role)
        return FakeProvider([_json({"s1": {"liberty": 0.2}})])

    monkeypatch.setattr(llm, "get_provider", factory)
    tags = chamber.tag_sections(
        {"sections": [{"section_id": "s1", "heading": "H", "summary": "S"}]}
    )
    assert roles == ["primary"]
    assert tags["s1"]["liberty"] == 0.2


def test_detect_conflicts_keeps_only_real_bills():
    payload = [
        {"bill_id": "b1", "conflict": "Repeals it."},
        {"bill_id": "hallucinated", "conflict": "Nope."},
        "not-an-object",
    ]
    fake = FakeProvider([_json(payload)])
    out = conflict.detect_conflicts(
        {"title": "New", "plain_summary": "A new rule.", "sections": [{"heading": "Scope"}]},
        [{"id": "b1", "title": "Old", "summary": "Old summary"}],
        provider=fake,
    )
    assert out == [{"bill_id": "b1", "title": "Old", "conflict": "Repeals it."}]
    assert fake.calls[0]["system"] is None
    assert fake.calls[0]["max_tokens"] == 1000
    assert "Old summary" in fake.calls[0]["user"]
    assert "A new rule." in fake.calls[0]["user"]


def test_detect_conflicts_accepts_wrapped_object_and_skips_empty():
    fake = FakeProvider([_json({"conflicts": [{"bill_id": "b1", "conflict": "Duplicates."}]})])
    out = conflict.detect_conflicts(
        {"title": "New", "plain_summary": "S", "sections": []},
        [{"id": "b1", "title": "Old", "summary": "x"}],
        provider=fake,
    )
    assert out == [{"bill_id": "b1", "title": "Old", "conflict": "Duplicates."}]

    idle = FakeProvider([])
    assert conflict.detect_conflicts({"title": "T"}, [], provider=idle) == []
    assert idle.calls == []


def test_detect_conflicts_requests_checker_role(monkeypatch):
    roles: list[str] = []

    def factory(role: str = "primary") -> FakeProvider:
        roles.append(role)
        return FakeProvider(["[]"])

    monkeypatch.setattr(llm, "get_provider", factory)
    assert conflict.detect_conflicts(
        {"title": "New", "plain_summary": "S", "sections": []},
        [{"id": "b1", "title": "Old", "summary": "x"}],
    ) == []
    assert roles == ["checker"]


# ── HTTP: missing credential stays an explicit 500, not an empty failure ─────


@pytest.fixture
def client():
    return TestClient(app)


def test_health_reports_configured_credential(client, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/health").json() == {
        "status": "ok",
        "api_key_configured": False,
    }
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["api_key_configured"] is True


def test_analyze_missing_key_is_explicit_500(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    response = client.post("/api/analyze", data={"text": "Section 1. " * 20})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "ANTHROPIC_API_KEY" in detail
    assert "console.anthropic.com" in detail


def test_analyze_unknown_provider_is_explicit_500(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    response = client.post("/api/analyze", data={"text": "Section 1. " * 20})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "gemini" in detail
    assert "anthropic" in detail


def test_analyze_endpoint_uses_provider_and_rejects_bad_json(client, monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_provider",
        lambda role="primary": FakeProvider([_json(ANALYSIS)]),
    )
    response = client.post("/api/analyze", data={"text": "Section 1. " * 20})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["analysis"]["title"] == "Plain title"

    monkeypatch.setattr(
        llm,
        "get_provider",
        lambda role="primary": FakeProvider(["not json"]),
    )
    bad = client.post("/api/analyze", data={"text": "Section 1. " * 20})
    assert bad.status_code == 502
    assert "unparseable" in bad.json()["detail"].lower()


def test_analyze_provider_failure_is_explicit_500(client, monkeypatch):
    class Upstream(Exception):
        pass

    class Boom(llm.LLMProvider):
        provider_id = "boom"
        model = "boom"

        def complete(self, *, user: str, system: str | None = None, max_tokens: int = 8000) -> str:
            raise Upstream("upstream down")

    monkeypatch.setattr(llm, "get_provider", lambda role="primary": Boom())
    response = client.post("/api/analyze", data={"text": "Section 1. " * 20})
    assert response.status_code == 500
    assert "Analysis failed: upstream down" in response.json()["detail"]
