"""
OpenAI-compatible provider, disagreement matching, and the vote policy.

No network and no API key. HTTP is a stand-in httpx client. Analysis uses
scripted providers.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import avatar, comparison, config, llm, summarizer
from backend.grounding import BILL_DATA_BEGIN, BILL_DATA_END
from backend.avatar import _norm_key
from backend.profile import CitizenProfile
from main import app


QUOTE = "the agency may search stored electronic mail without a warrant"
SUBSIDY = "a subsidy shall be paid to each pharmaceutical manufacturer"


class FakeProvider(llm.LLMProvider):
    provider_id = "fake"

    def __init__(self, responses: list[Any], model: str = "fake-model"):
        self.model = model
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


class FakeHttp:
    """Context-manager stand-in for httpx.Client. Records the one POST."""

    posts: list[dict[str, Any]] = []
    response: httpx.Response | None = None
    init_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any):
        FakeHttp.init_kwargs = kwargs

    def __enter__(self) -> "FakeHttp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, json: Any = None, headers: Any = None) -> httpx.Response:
        FakeHttp.posts.append({"url": url, "json": json, "headers": headers})
        if FakeHttp.response is not None:
            return FakeHttp.response
        return httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", url))


def _analysis(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Plain title",
        "plain_summary": "It does a thing.",
        "sections": [
            {
                "section_id": "Sec. 101",
                "heading": "Search",
                "summary": "Allows a search of stored email without a warrant.",
            }
        ],
        "beneficiaries": {"benefits": ["farmers"], "costs": ["taxpayers"], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _report(primary: dict[str, Any], other: dict[str, Any], policy: str = "abstain") -> dict[str, Any]:
    return comparison.build_report(
        primary,
        other,
        primary_info={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        comparison_info={"provider": "openai", "model": "llama3.1:8b"},
        policy=policy,
    )


def _profile() -> CitizenProfile:
    return CitizenProfile(values="Protect privacy.", compass={"liberty": 0.4})


def _vote(position: str = "yes") -> dict[str, Any]:
    return {
        "section_votes": [
            {
                "section_id": "s1",
                "heading": "Search",
                "position": position,
                "basis": "values",
                "reasoning": "Privacy.",
                "confidence": "high",
            },
            {
                "section_id": "s2",
                "heading": "Funding",
                "position": "yes",
                "basis": "values",
                "reasoning": "Fine.",
                "confidence": "high",
            },
        ],
        "recommendation": "Vote yes.",
        "would_pass_sections": ["s1", "Sec. 101"],
        "key_tension": "",
    }


@pytest.fixture
def client():
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture(autouse=True)
def _reset_http():
    FakeHttp.posts = []
    FakeHttp.response = None
    FakeHttp.init_kwargs = {}
    yield
    FakeHttp.posts = []
    FakeHttp.response = None


# ── request and response shape ────────────────────────────────────────────────


def test_chat_completions_url_joins_without_doubling():
    assert llm.chat_completions_url("https://api.openai.com/v1") == (
        "https://api.openai.com/v1/chat/completions"
    )
    assert llm.chat_completions_url("https://api.openai.com/v1/") == (
        "https://api.openai.com/v1/chat/completions"
    )
    assert llm.chat_completions_url("http://127.0.0.1:11434/v1/chat/completions") == (
        "http://127.0.0.1:11434/v1/chat/completions"
    )
    assert llm.chat_completions_url("") == "https://api.openai.com/v1/chat/completions"


def test_openai_request_and_response_shape(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "CHECKER_MODEL", "claude-haiku-4-5")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "http://openai.test/v1/")
    monkeypatch.setattr(config, "OPENAI_TIMEOUT", 15.0)
    monkeypatch.setattr(llm.httpx, "Client", FakeHttp)
    FakeHttp.response = httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "```json\n" + json.dumps({"ok": True}) + "\n```",
                    },
                    "finish_reason": "stop",
                }
            ],
        },
        request=httpx.Request("POST", "http://openai.test/v1/chat/completions"),
    )

    provider = llm.get_provider("primary")
    assert isinstance(provider, llm.OpenAICompatibleProvider)
    assert provider.model == "gpt-4o-mini"
    parsed = provider.complete_json(user="the bill", system="be neutral", max_tokens=8000)
    assert parsed == {"ok": True}

    assert len(FakeHttp.posts) == 1
    post = FakeHttp.posts[0]
    assert post["url"] == "http://openai.test/v1/chat/completions"
    assert post["headers"]["Authorization"] == "Bearer sk-test"
    assert post["headers"]["Content-Type"] == "application/json"
    assert post["json"] == {
        "model": "gpt-4o-mini",
        "max_tokens": 8000,
        "messages": [
            {"role": "system", "content": "be neutral"},
            {"role": "user", "content": "the bill"},
        ],
    }
    assert "claude" not in json.dumps(post["json"])
    assert "sk-test" not in json.dumps(post["json"])
    assert FakeHttp.init_kwargs["timeout"] == 15.0


def test_openai_omits_system_message_and_reads_list_content(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "OPENAI_MODEL", "local-model")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "http://127.0.0.1:8001/v1")
    monkeypatch.setattr(llm.httpx, "Client", FakeHttp)
    FakeHttp.response = httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "hello "},
                            {"type": "text", "text": "there"},
                        ],
                    }
                }
            ]
        },
        request=httpx.Request("POST", "http://127.0.0.1:8001/v1/chat/completions"),
    )
    provider = llm.OpenAICompatibleProvider(model="local-model")
    assert provider.complete(user="only user", max_tokens=12) == "hello there"
    assert FakeHttp.posts[0]["json"]["messages"] == [{"role": "user", "content": "only user"}]
    assert FakeHttp.posts[0]["json"]["max_tokens"] == 12
    assert FakeHttp.posts[0]["json"]["model"] == "local-model"


def test_openai_error_status_and_empty_choices(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setattr(llm.httpx, "Client", FakeHttp)
    url = "http://openai.test/v1/chat/completions"
    FakeHttp.response = httpx.Response(
        401,
        json={"error": {"message": "bad key sk-secret-body-echo"}},
        request=httpx.Request("POST", url),
    )
    provider = llm.OpenAICompatibleProvider(model="gpt-4o", base_url="http://openai.test/v1")
    with pytest.raises(RuntimeError, match="HTTP 401") as exc:
        provider.complete(user="hi", max_tokens=8)
    assert "sk-secret" not in str(exc.value)
    assert "body-echo" not in str(exc.value)

    FakeHttp.response = httpx.Response(
        200, json={"choices": []}, request=httpx.Request("POST", url)
    )
    with pytest.raises(ValueError, match="no choices"):
        provider.complete(user="hi", max_tokens=8)


def test_openai_does_not_send_anthropic_model_ids(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "CHECKER_PROVIDER", "openai")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "CHECKER_MODEL", "claude-haiku-4-5")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o")
    monkeypatch.setattr(config, "OPENAI_CHECKER_MODEL", "")

    primary = llm.get_provider("primary")
    checker = llm.get_provider("checker")
    assert primary.model == "gpt-4o"
    assert checker.model == "gpt-4o"
    assert "claude" not in primary.model
    assert "claude" not in checker.model

    monkeypatch.setattr(config, "OPENAI_CHECKER_MODEL", "gpt-4o-mini")
    assert llm.get_provider("checker").model == "gpt-4o-mini"


def test_missing_openai_key_does_not_call_http(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("HTTP client was constructed")

    monkeypatch.setattr(llm.httpx, "Client", boom)
    with pytest.raises(llm.LLMConfigError, match="OPENAI_API_KEY") as exc:
        llm.get_provider("primary")
    assert isinstance(exc.value, RuntimeError)

    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    with pytest.raises(llm.LLMConfigError, match="OPENAI_API_KEY"):
        llm.OpenAICompatibleProvider(model="gpt-4o")


def test_missing_openai_model_names_the_setting_and_skips_claude_ids(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "OPENAI_MODEL", "")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("HTTP client was constructed")

    monkeypatch.setattr(llm.httpx, "Client", boom)
    with pytest.raises(llm.LLMConfigError, match="OPENAI_MODEL") as exc:
        llm.get_provider("primary")
    assert "Anthropic model ids are not sent" in str(exc.value)
    assert "claude" not in str(exc.value)


def test_comparison_model_is_required_and_not_taken_from_avatar_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o")
    with pytest.raises(llm.LLMConfigError, match="COMPARISON_MODEL"):
        llm.get_provider("comparison")

    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    provider = llm.get_provider("comparison")
    assert provider.provider_id == "openai"
    assert provider.model == "llama3.1:8b"


def test_unset_comparison_provider_is_a_config_error(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    with pytest.raises(llm.LLMConfigError, match="COMPARISON_PROVIDER"):
        llm.get_provider("comparison")


def test_openai_credentials_follow_the_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.credentials_configured("openai") is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.credentials_configured("openai") is True


# ── disagreement matching ─────────────────────────────────────────────────────


def test_section_norm_matches_the_avatar_and_quote_grounding_rules():
    for value in ("Sec. 101", "sec_101", "Section 101", "101", "multiple"):
        assert comparison.norm_section_id(value) == _norm_key(value)


def test_same_section_id_with_the_same_summary_is_not_a_disagreement():
    primary = _analysis(
        sections=[
            {
                "section_id": "Sec. 1",
                "heading": "Title",
                "summary": "Sets the short title of the act for public citation.",
            }
        ]
    )
    other = _analysis(
        sections=[
            {
                "section_id": "Section 1",
                "heading": "Name",
                "summary": "Sets the short title of the act for public citation.",
            }
        ],
        beneficiaries={"benefits": ["farmers"], "costs": ["taxpayers"], "regulated": []},
    )
    report = _report(primary, other)
    assert report["sections_only_primary"] == []
    assert report["sections_only_comparison"] == []
    assert report["sections_materially_different"] == []
    assert report["beneficiary_differences"] == []
    assert report["high_severity_disagreements"] == []
    assert report["enabled"] is True
    assert report["policy"] == "abstain"


def test_section_only_one_model_and_materially_different_summaries():
    primary = _analysis(
        sections=[
            {
                "section_id": "Sec. 101",
                "heading": "Wages",
                "summary": "Raises the minimum wage to 15 dollars an hour for covered workers.",
            },
            {
                "section_id": "Sec. 202",
                "heading": "Parks",
                "summary": "Funds wildlife habitat restoration in national forests.",
            },
        ]
    )
    other = _analysis(
        sections=[
            {
                "section_id": "Sec. 101",
                "heading": "Wages",
                "summary": "Cuts the corporate tax rate for large manufacturers in this section.",
            }
        ]
    )
    report = _report(primary, other)
    assert [row["section_id"] for row in report["sections_only_primary"]] == ["Sec. 202"]
    assert report["sections_only_comparison"] == []
    different = report["sections_materially_different"]
    assert len(different) == 1
    assert different[0]["section_id"] == "Sec. 101"
    assert different[0]["matched_by"] == "section_id"
    assert "minimum wage" in different[0]["primary_summary"]
    assert "tax rate" in different[0]["comparison_summary"]


def test_grounded_quotes_pair_claims_that_do_not_share_a_section_id():
    primary = _analysis(
        sections=[
            {
                "section_id": "Sec. 101",
                "heading": "Search",
                "summary": "Allows warrantless search of stored email.",
                "quote": QUOTE,
                "grounded": True,
            }
        ]
    )
    other = _analysis(
        sections=[
            {
                "section_id": "Part B",
                "heading": "Mail",
                "summary": "Creates a new national park in the northern range.",
                "quote": QUOTE,
                "grounded": True,
            }
        ]
    )
    report = _report(primary, other)
    assert report["sections_only_primary"] == []
    assert report["sections_only_comparison"] == []
    row = report["sections_materially_different"][0]
    assert row["matched_by"] == "quote"
    assert row["primary_quote"] == QUOTE
    assert row["primary_quote_grounded"] is True
    assert row["comparison_section_id"] == "Part B"


def test_ungrounded_quotes_are_not_used_to_match():
    primary = _analysis(
        sections=[
            {
                "section_id": "Sec. 101",
                "heading": "Search",
                "summary": "Allows warrantless search of stored email.",
                "quote": QUOTE,
                "grounded": False,
            }
        ]
    )
    other = _analysis(
        sections=[
            {
                "section_id": "Part B",
                "heading": "Parks",
                "summary": "Creates a new national park in the northern range.",
                "quote": QUOTE,
                "grounded": False,
            }
        ]
    )
    report = _report(primary, other)
    assert [row["section_id"] for row in report["sections_only_primary"]] == ["Sec. 101"]
    assert [row["section_id"] for row in report["sections_only_comparison"]] == ["Part B"]
    assert report["sections_only_primary"][0]["quote_grounded"] is False
    assert report["sections_materially_different"] == []


def test_red_flags_only_one_model_and_severity_mismatch():
    shared = {
        "type": "broad_delegation",
        "section": "Sec. 101",
        "description": "The agency gets open-ended search power with no warrant and no sunset.",
        "severity": "high",
    }
    primary = _analysis(red_flags=[shared, {
        "type": "exemption",
        "section": "Sec. 202",
        "description": "One named company is exempt from the reporting duty.",
        "severity": "medium",
    }])
    other = _analysis(red_flags=[{**shared, "severity": "low", "description": "Delegates some search authority."}])
    report = _report(primary, other)
    assert [flag["type"] for flag in report["red_flags_only_primary"]] == ["exemption"]
    assert report["red_flags_only_comparison"] == []
    high = report["high_severity_disagreements"]
    assert len(high) == 1
    assert high[0]["kind"] == "severity"
    assert high[0]["raised_by"] == "primary"
    assert high[0]["other_severity"] == "low"
    assert high[0]["type"] == "broad_delegation"
    assert high[0]["targets_all"] is False


def test_matching_high_flags_are_not_a_disagreement():
    flag = {
        "type": "broad_delegation",
        "section": "Section 101",
        "description": "Open-ended authority.",
        "severity": "high",
    }
    other_flag = {**flag, "section": "Sec. 101", "description": "Open-ended authority."}
    report = _report(_analysis(red_flags=[flag]), _analysis(red_flags=[other_flag]))
    assert report["red_flags_only_primary"] == []
    assert report["red_flags_only_comparison"] == []
    assert report["high_severity_disagreements"] == []


def test_high_flag_the_other_model_did_not_raise():
    flag = {
        "type": "buried_provision",
        "section": "Sec. 101",
        "description": "A surveillance power sits in the definitions section.",
        "severity": "high",
        "quote": QUOTE,
        "grounded": True,
    }
    report = _report(_analysis(red_flags=[flag]), _analysis(red_flags=[]))
    assert report["red_flags_only_primary"][0]["type"] == "buried_provision"
    assert report["red_flags_only_primary"][0]["quote"] == QUOTE
    assert report["red_flags_only_primary"][0]["quote_grounded"] is True
    high = report["high_severity_disagreements"][0]
    assert high["kind"] == "unmatched"
    assert high["raised_by"] == "primary"
    assert high["quote"] == QUOTE


def test_blank_or_multiple_high_flag_targets_every_section():
    for label in ("", "multiple"):
        flag = {
            "type": "vague_language",
            "section": label,
            "description": "Key terms are undefined across the bill.",
            "severity": "high",
        }
        report = _report(_analysis(red_flags=[flag]), _analysis())
        assert report["high_severity_disagreements"][0]["targets_all"] is True


def test_beneficiary_strings_and_quote_bearing_claims():
    primary = _analysis(
        beneficiaries={"benefits": ["farmers"], "costs": ["taxpayers"], "regulated": []},
        beneficiary_claims=[
            {
                "group": "benefits",
                "claim": "drug makers",
                "quote": SUBSIDY,
                "grounded": True,
            }
        ],
    )
    other = _analysis(
        beneficiaries={"benefits": ["farmers"], "costs": [], "regulated": []},
        beneficiary_claims=[
            {
                "group": "costs",
                "claim": "taxpayers fund drug companies",
                "quote": SUBSIDY,
                "grounded": True,
            }
        ],
    )
    report = _report(primary, other)
    # beneficiary_claims wins over the flattened string lists, so "farmers"
    # agreeing on both sides is not what gets compared.
    assert report["beneficiary_differences"] == []
    conflict = report["beneficiary_group_conflicts"][0]
    assert conflict["primary_group"] == "benefits"
    assert conflict["comparison_group"] == "costs"
    assert conflict["matched_by"] == "quote"
    assert conflict["primary_quote_grounded"] is True

    strings = _report(
        _analysis(beneficiaries={"benefits": ["farmers"], "costs": [], "regulated": []}),
        _analysis(beneficiaries={"benefits": ["insurers"], "costs": ["counties"], "regulated": []}),
    )
    groups = {row["group"]: row for row in strings["beneficiary_differences"]}
    assert [item["claim"] for item in groups["benefits"]["only_primary"]] == ["farmers"]
    assert [item["claim"] for item in groups["benefits"]["only_comparison"]] == ["insurers"]
    assert [item["claim"] for item in groups["costs"]["only_comparison"]] == ["counties"]
    assert strings["beneficiary_group_conflicts"] == []


def test_report_is_deterministic():
    primary = _analysis(red_flags=[{
        "type": "exemption",
        "section": "Sec. 3",
        "description": "A single contractor is exempt.",
        "severity": "high",
    }])
    other = _analysis()
    assert _report(primary, other) == _report(primary, other)


# ── vote policy ───────────────────────────────────────────────────────────────


def _votes():
    data = _vote()
    return [
        avatar.SectionVote(
            section_id=item["section_id"],
            heading=item["heading"],
            position=item["position"],
            basis=item["basis"],
            reasoning=item["reasoning"],
            confidence=item["confidence"],
        )
        for item in data["section_votes"]
    ]


def test_abstain_policy_holds_the_section_and_does_not_keep_the_model_position():
    report = _report(
        _analysis(red_flags=[{
            "type": "broad_delegation",
            "section": "Sec. 101",
            "description": "No guardrails on the search power.",
            "severity": "high",
        }]),
        _analysis(),
    )
    # The vote ids use a different spelling. Normalized ids still match.
    votes = _votes()
    votes[0].section_id = "sec_101"
    analysis = {"title": "Plain title", "model_comparison": report}
    abstained = comparison.apply_vote_policy(votes, analysis)
    assert votes[0].position == "abstain"
    assert votes[0].comparison_abstained is True
    assert votes[0].needs_human_review is False
    assert votes[0].comparison_prior_position == "yes"
    assert votes[0].confidence == "low"
    assert "neither model overrode" in votes[0].comparison_note
    assert "sec_101" in abstained
    assert votes[1].position == "yes"
    assert votes[1].comparison_abstained is False
    would = comparison.without_abstained(["sec_101", "s2"], abstained)
    assert would == ["s2"]


def test_multiple_high_flag_abstains_every_section():
    report = _report(
        _analysis(red_flags=[{
            "type": "vague_language",
            "section": "multiple",
            "description": "Undefined terms run through the operative sections.",
            "severity": "high",
        }]),
        _analysis(),
    )
    votes = _votes()
    abstained = comparison.apply_vote_policy(votes, {"model_comparison": report})
    assert abstained == {"s1", "s2"}
    assert all(vote.position == "abstain" for vote in votes)
    assert comparison.without_abstained(["s1"], set()) == ["s1"]
    assert comparison.without_abstained("not-a-list", set()) == "not-a-list"


def test_review_policy_keeps_the_position_and_flags_it():
    report = _report(
        _analysis(),
        _analysis(red_flags=[{
            "type": "hidden_funding",
            "section": "s1",
            "description": "Appropriates money for a program the title does not name.",
            "severity": "high",
        }]),
        policy="review",
    )
    votes = _votes()
    abstained = comparison.apply_vote_policy(votes, {"model_comparison": report})
    assert abstained == set()
    assert votes[0].position == "yes"
    assert votes[0].needs_human_review is True
    assert votes[0].comparison_abstained is False
    assert "human review" in votes[0].comparison_note
    assert "not withheld" in votes[0].comparison_note
    assert votes[0].position == "yes"
    assert votes[1].needs_human_review is False
    assert comparison.without_abstained(["s1"], abstained) == ["s1"]


def test_no_report_leaves_the_vote_unchanged():
    votes = _votes()
    assert comparison.apply_vote_policy(votes, {"title": "Plain title"}) == set()
    assert votes[0].position == "yes"
    assert votes[0].comparison_abstained is False
    assert votes[0].comparison_note == ""


def test_medium_flag_does_not_change_the_vote():
    report = _report(
        _analysis(red_flags=[{
            "type": "exemption",
            "section": "s1",
            "description": "One company is exempt from a reporting duty.",
            "severity": "medium",
        }]),
        _analysis(),
    )
    assert report["high_severity_disagreements"] == []
    votes = _votes()
    assert comparison.apply_vote_policy(votes, {"model_comparison": report}) == set()
    assert votes[0].position == "yes"


def test_cast_vote_applies_abstain_and_filters_would_pass(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    report = _report(
        _analysis(red_flags=[{
            "type": "broad_delegation",
            "section": "s1",
            "description": "No guardrails.",
            "severity": "high",
        }]),
        _analysis(),
    )
    analysis = {
        "title": "Surveillance Bill",
        "sections": [{"section_id": "s1", "heading": "Search", "summary": "Search."}],
        "red_flags": [],
        "model_comparison": report,
    }
    fake = FakeProvider([json.dumps(_vote("yes"))])
    result = avatar.cast_vote(analysis, _profile(), provider=fake)
    held = result.section_votes[0]
    assert held.position == "abstain"
    assert held.comparison_prior_position == "yes"
    assert held.comparison_abstained is True
    assert result.section_votes[1].position == "yes"
    assert result.would_pass_sections == ["Sec. 101"]
    assert len(fake.calls) == 1


def test_cast_vote_without_comparison_keeps_the_model_position(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    fake = FakeProvider([json.dumps(_vote("no"))])
    analysis = {
        "title": "Surveillance Bill",
        "sections": [{"section_id": "s1", "heading": "Search", "summary": "Search."}],
        "red_flags": [],
    }
    result = avatar.cast_vote(analysis, _profile(), provider=fake)
    assert result.section_votes[0].position == "no"
    assert result.section_votes[0].comparison_abstained is False
    assert result.section_votes[0].needs_human_review is False
    assert result.would_pass_sections == ["s1", "Sec. 101"]


# ── orchestration: same prompt, missing key, API shape ───────────────────────


def test_comparison_uses_the_same_prompt_and_schema(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    monkeypatch.setattr(config, "COMPARISON_VOTE_POLICY", "abstain")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    primary = FakeProvider([json.dumps(_analysis())], model="ignored")
    other_body = _analysis(
        sections=[{
            "section_id": "Sec. 101",
            "heading": "Search",
            "summary": "Bans the export of timber from public land for ten years.",
        }],
        red_flags=[{
            "type": "buried_provision",
            "section": "Sec. 101",
            "description": "The export ban is unrelated to the stated search power.",
            "severity": "high",
        }],
    )
    other = FakeProvider([json.dumps(other_body)], model="llama3.1:8b")
    other.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    text = "Section 101. The agency may search stored mail. " * 4
    result = comparison.analyze_for_api(text)
    assert primary.calls[0]["system"] == other.calls[0]["system"] == summarizer.SYSTEM_PROMPT
    assert primary.calls[0]["user"] == other.calls[0]["user"]
    assert text in primary.calls[0]["user"]
    assert primary.calls[0]["user"].startswith(summarizer.ANALYSIS_PROMPT)
    report = result["model_comparison"]
    assert report["comparison"]["model"] == "llama3.1:8b"
    assert report["primary"]["provider"] == "anthropic"
    assert report["primary"]["model"] == "claude-sonnet-4-6"
    assert report["high_severity_disagreements"][0]["raised_by"] == "comparison"
    assert report["sections_materially_different"][0]["matched_by"] == "section_id"


def test_no_comparison_provider_does_not_call_a_second_model(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    roles: list[str] = []

    def factory(role: str = "primary") -> FakeProvider:
        roles.append(role)
        return FakeProvider([json.dumps(_analysis())])

    monkeypatch.setattr(llm, "get_provider", factory)
    result = comparison.analyze_for_api("A bill about farms. " * 5)
    assert roles == ["primary"]
    assert "model_comparison" not in result
    assert result["title"] == "Plain title"


def test_missing_comparison_key_fails_before_the_primary_call(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    roles: list[str] = []
    real = llm.get_provider

    def factory(role: str = "primary") -> llm.LLMProvider:
        roles.append(role)
        return real(role)

    monkeypatch.setattr(llm, "get_provider", factory)
    with pytest.raises(llm.LLMConfigError, match="OPENAI_API_KEY"):
        comparison.analyze_for_api("Section 1. " * 20)
    assert roles == ["comparison"]


def test_analyze_api_includes_the_report_and_omits_it_when_unset(client, monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")
    monkeypatch.setattr(
        llm,
        "get_provider",
        lambda role="primary": FakeProvider([json.dumps(_analysis())]),
    )
    off = client.post("/api/analyze", files={"text": (None, "Section 1. " * 20)})
    assert off.status_code == 200
    off_body = off.json()
    assert off_body["success"] is True
    assert off_body["analysis"]["title"] == "Plain title"
    assert "model_comparison" not in off_body
    assert "model_comparison" not in off_body["analysis"]

    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_VOTE_POLICY", "abstain")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    other = _analysis(red_flags=[{
        "type": "hidden_funding",
        "section": "Sec. 101",
        "description": "Money for a program the title does not mention.",
        "severity": "high",
    }])

    def factory(role: str = "primary") -> FakeProvider:
        if role == "comparison":
            provider = FakeProvider([json.dumps(other)], model="gpt-4o")
            provider.provider_id = "openai"
            return provider
        return FakeProvider([json.dumps(_analysis())])

    monkeypatch.setattr(llm, "get_provider", factory)
    on = client.post("/api/analyze", files={"text": (None, "Section 1. " * 20)})
    assert on.status_code == 200
    body = on.json()
    assert body["model_comparison"]["enabled"] is True
    assert body["model_comparison"]["high_severity_disagreements"][0]["type"] == "hidden_funding"
    assert body["analysis"]["model_comparison"]["policy"] == "abstain"
    assert body["model_comparison"] == body["analysis"]["model_comparison"]


SEARCH_SENTENCE = (
    "The agency may search stored electronic mail without a warrant "
    "issued by any court of competent jurisdiction."
)
FAKE_SENTENCE = (
    "The agency may seize private diaries without notice and without "
    "any later review by a court of competent jurisdiction."
)
GROUNDED_BILL = (
    "Sec. 101. Search authority.\n"
    + SEARCH_SENTENCE
    + "\n\nSec. 202. Parks.\n"
    + "The secretary shall fund wildlife habitat restoration in national forests "
    + "for a period of ten full years.\n"
)


def test_comparison_fences_retries_schema_and_grounds_quotes(monkeypatch):
    """The comparison model uses the same fence, one schema retry, and quote check."""
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    monkeypatch.setattr(config, "COMPARISON_VOTE_POLICY", "abstain")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")

    primary_body = _analysis(
        sections=[{
            "section_id": "Sec. 101",
            "heading": "Search",
            "summary": "Allows a search of stored email without a warrant.",
            "quote": SEARCH_SENTENCE,
        }],
        beneficiaries={
            "benefits": [{"claim": "drug makers", "quote": SEARCH_SENTENCE}],
            "costs": [],
            "regulated": [],
        },
    )
    comparison_body = _analysis(
        sections=[{
            "section_id": "Sec. 101",
            "heading": "Search",
            "summary": "Cuts the timber export tax for large manufacturers in this section.",
            "quote": FAKE_SENTENCE,
        }],
        beneficiaries={
            "benefits": [],
            "costs": [{"claim": "taxpayers fund drug companies", "quote": SEARCH_SENTENCE}],
            "regulated": [],
        },
    )
    primary = FakeProvider([json.dumps(primary_body)])
    other = FakeProvider(["not json", json.dumps(comparison_body)], model="llama3.1:8b")
    other.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    result = comparison.analyze_for_api(GROUNDED_BILL)

    assert len(primary.calls) == 1
    assert len(other.calls) == 2
    for call in primary.calls + other.calls:
        assert call["system"] == summarizer.SYSTEM_PROMPT
        assert call["user"].startswith(summarizer.ANALYSIS_PROMPT)
        before, fenced = call["user"].split(BILL_DATA_BEGIN, 1)
        assert SEARCH_SENTENCE not in before
        assert SEARCH_SENTENCE in fenced
        assert BILL_DATA_END in fenced
    assert "previous response was rejected" in other.calls[1]["user"]
    assert "previous response was rejected" not in other.calls[0]["user"]

    section = result["sections"][0]
    assert section["grounded"] is True
    assert section["quote"] == SEARCH_SENTENCE
    compared = result["model_comparison"]["sections_materially_different"][0]
    assert compared["matched_by"] == "section_id"
    assert compared["primary_quote_grounded"] is True
    assert compared["comparison_quote_grounded"] is False
    assert compared["comparison_quote"] == FAKE_SENTENCE
    conflict = result["model_comparison"]["beneficiary_group_conflicts"][0]
    assert conflict["matched_by"] == "quote"
    assert conflict["primary_quote_grounded"] is True
    assert conflict["comparison_quote_grounded"] is True
    assert conflict["primary_group"] == "benefits"
    assert conflict["comparison_group"] == "costs"


def test_comparison_document_blocks_keep_the_bill_out_of_the_instruction(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")

    class DocumentProvider(FakeProvider):
        def accepts_data_blocks(self) -> bool:
            return True

        def complete(
            self,
            *,
            user: str,
            system: str | None = None,
            max_tokens: int = 8000,
            data_blocks: list[llm.DataBlock] | None = None,
        ) -> str:
            self.calls.append({
                "user": user,
                "system": system,
                "max_tokens": max_tokens,
                "data_blocks": list(data_blocks or []),
            })
            item = self._responses.pop(0)
            return item

    primary = DocumentProvider([json.dumps(_analysis())])
    other = DocumentProvider([json.dumps(_analysis())], model="llama3.1:8b")

    def factory(role: str = "primary") -> DocumentProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    comparison.analyze_for_api(GROUNDED_BILL)

    for provider in (primary, other):
        call = provider.calls[0]
        assert SEARCH_SENTENCE not in call["user"]
        assert BILL_DATA_BEGIN not in call["user"]
        assert "separate data document" in call["user"]
        assert len(call["data_blocks"]) == 1
        assert call["data_blocks"][0].name == "bill"
        assert SEARCH_SENTENCE in call["data_blocks"][0].text


SHALL = "The agency shall publish the names of every registered donor in the annual report."
SHALL_NOT = "The agency shall not publish the names of every registered donor in the annual report."
SAME_QUOTE = "The agency shall publish the names of every registered donor who gave more than 200 dollars."


def _high_flag(description: str, **extra: Any) -> dict[str, Any]:
    flag = {
        "type": "buried_provision",
        "section": "s1",
        "description": description,
        "severity": "high",
    }
    flag.update(extra)
    return flag


def test_negation_and_numbers_block_a_match():
    assert comparison.texts_match("shall", "shall not") is False
    assert comparison.texts_match("shall", "shall") is True
    assert comparison.quotes_match(SHALL, SHALL_NOT) is False
    assert comparison.quotes_match(SHALL, SHALL) is True
    assert comparison.texts_match("may publish the list", "may not publish the list") is False
    assert comparison.texts_match("always publish", "never publish") is False
    assert comparison.texts_match("all donors", "no donors") is False
    assert comparison.texts_match("the rule applies", "the rule applies except charities") is False
    assert comparison.texts_match("the rule applies", "the rule applies unless waived") is False
    assert comparison.texts_match("search with a warrant", "search without a warrant") is False
    assert comparison.texts_match("wolno publikowac dane", "nie wolno publikowac dane") is False
    assert comparison.texts_match("skal publisere navnene", "skal ikke publisere navnene") is False
    assert comparison.texts_match("ingen unntak gjelder her", "unntak gjelder her") is False
    # "notice" is not the word "not".
    assert comparison._negations("notice the deadline") == frozenset()
    assert comparison.texts_match("raise the wage to 15 dollars", "raise the wage to 16 dollars") is False
    assert comparison.texts_match("a fee of 15%", "a fee of 15") is False
    assert comparison.texts_match("a fee of $1,000", "a fee of $2,000") is False
    assert comparison.texts_match("a fee of $1,000", "a fee of $1000") is False
    assert comparison.texts_match("effective 2026-01-02", "effective 2026-01-03") is False


def test_paired_high_flags_with_opposite_words_abstain(monkeypatch):
    """Critic: shall against shall not, same section and type, must not stay yes."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    report = _report(
        _analysis(red_flags=[_high_flag(SHALL)]),
        _analysis(red_flags=[_high_flag(SHALL_NOT)]),
    )
    assert report["high_severity_disagreements"]
    assert report["high_severity_disagreements"][0]["kind"] == "content"
    analysis = {
        "title": "Donor bill",
        "sections": [{"section_id": "s1", "heading": "Names", "summary": "Names."}],
        "red_flags": [],
        "model_comparison": report,
    }
    result = avatar.cast_vote(analysis, _profile(), provider=FakeProvider([json.dumps(_vote("yes"))]))
    assert result.section_votes[0].position == "abstain"
    assert result.section_votes[0].comparison_abstained is True
    assert result.section_votes[0].comparison_prior_position == "yes"
    assert "s1" not in result.would_pass_sections


def test_same_quote_with_opposite_conclusions_is_a_disagreement():
    report = _report(
        _analysis(red_flags=[_high_flag(
            "The agency shall publish those names.",
            quote=SAME_QUOTE,
            grounded=True,
        )]),
        _analysis(red_flags=[_high_flag(
            "The agency shall not publish those names.",
            quote=SAME_QUOTE,
            grounded=True,
        )]),
    )
    assert report["high_severity_disagreements"]
    assert report["high_severity_disagreements"][0]["kind"] == "content"
    assert report["red_flags_only_primary"] == []
    assert report["red_flags_only_comparison"] == []


def test_negated_overlap_across_types_is_not_agreement():
    report = _report(
        _analysis(red_flags=[{
            "type": "broad_delegation",
            "section": "Sec. 101",
            "description": SHALL_NOT,
            "severity": "high",
        }]),
        _analysis(red_flags=[{
            "type": "exemption",
            "section": "Sec. 202",
            "description": SHALL,
            "severity": "high",
        }]),
    )
    assert report["high_severity_disagreements"]
    assert all(item["kind"] == "unmatched" for item in report["high_severity_disagreements"])
    summaries = _report(
        _analysis(sections=[{
            "section_id": "Sec. 101",
            "heading": "Names",
            "summary": SHALL,
        }]),
        _analysis(sections=[{
            "section_id": "Sec. 101",
            "heading": "Names",
            "summary": SHALL_NOT,
        }]),
    )
    assert summaries["sections_materially_different"]
    assert summaries["sections_materially_different"][0]["matched_by"] == "section_id"


def test_ungrounded_high_flag_is_not_paired_as_agreement():
    report = _report(
        _analysis(red_flags=[_high_flag(SHALL, grounded=False, quote="not in the bill at all")]),
        _analysis(red_flags=[_high_flag(SHALL, grounded=True, quote=SAME_QUOTE)]),
    )
    assert report["red_flags_only_primary"]
    assert report["red_flags_only_comparison"]
    assert report["high_severity_disagreements"]
    assert all(item["kind"] == "unmatched" for item in report["high_severity_disagreements"])


def test_reversed_number_range_is_a_disagreement_and_abstains(monkeypatch):
    """A tax band from 5 to 10 is not a band from 10 to 5. The yes vote does not stand."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    low_to_high = "The tax moves from 5 to 10 percent."
    high_to_low = "The tax moves from 10 to 5 percent."
    assert comparison._numbers(low_to_high) == ("5", "10")
    assert comparison._numbers(high_to_low) == ("10", "5")
    assert comparison.texts_match(low_to_high, high_to_low) is False
    report = _report(
        _analysis(red_flags=[_high_flag(low_to_high)]),
        _analysis(red_flags=[_high_flag(high_to_low)]),
    )
    assert report["high_severity_disagreements"]
    assert report["high_severity_disagreements"][0]["kind"] == "content"
    analysis = {
        "title": "Tax bill",
        "sections": [{"section_id": "s1", "heading": "Rate", "summary": "Rate."}],
        "red_flags": [],
        "model_comparison": report,
    }
    result = avatar.cast_vote(
        analysis, _profile(), provider=FakeProvider([json.dumps(_vote("yes"))])
    )
    assert result.section_votes[0].position == "abstain"
    assert result.section_votes[0].comparison_abstained is True
    assert "s1" not in result.would_pass_sections


def test_number_formats_and_ranges_that_mean_the_same_span():
    assert comparison.texts_match("from 5 to 10", "5–10") is True
    assert comparison.texts_match("from 5 to 10", "5-10") is True
    assert comparison._numbers("1,000") == ("<ambiguous>",)
    assert comparison._numbers("1000") == ("1000",)
    assert comparison._numbers("1 000") == ("1 000",)
    assert comparison._numbers("1\u00a0000 zł") == ("1 000",)
    assert comparison._numbers("1\u202f000 kr") == ("1 000",)
    assert comparison.texts_match("the fee is 1 000 zł", "the fee is 1000 zł") is False
    assert comparison._numbers("1,5") == comparison._numbers("1.5") == ("1.5",)
    assert comparison._numbers("1,50") == ("1.5",)


def test_dated_model_alias_is_refused_and_a_vague_suffix_warns(monkeypatch):
    assert comparison.model_relationship(
        "gpt-4o", "openai", "openai/gpt-4o-2024-08-06", "openai"
    ) == "same_vendor"
    assert comparison.model_relationship(
        "gpt-4o", "openai", "gpt-4o-latest", "openai"
    ) == "same_vendor"
    assert comparison.model_relationship(
        "gpt-4o", "openai", "gpt-4o-mini", "openai"
    ) == "same_vendor"
    assert comparison.model_relationship(
        "claude-sonnet-4-6", "anthropic", "claude-haiku-4-5", "anthropic"
    ) == "same_vendor"
    assert comparison.model_relationship(
        "gpt-4o", "openai", "claude-sonnet-4-6", "anthropic"
    ) == "different"
    assert comparison.model_relationship(
        "meta-llama/Llama-3.1-8B", "openai", "qwen/Qwen2-7B", "openai"
    ) == "different"

    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "claude-sonnet-4-6-2024-08-06")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(llm.LLMConfigError, match="not an independent check"):
        comparison.analyze_for_api("Sec. 101. A short bill about taxes and rates.")


def test_european_dot_thousands_are_explicit_and_ambiguous_dots_fail(monkeypatch):
    """1.500 is not resolved. 1.500,00 is 1500. Polish and Norwegian forms too."""
    assert comparison._numbers("1.500") == ("<ambiguous>",)
    assert comparison._numbers("1.500,00") == ("1500",)
    assert comparison._numbers("1,500.00") == ("1500",)
    assert comparison._numbers("1.500.000") == ("1500000",)
    assert comparison.texts_match("the fee is 1.500", "the fee is 1500") is False
    assert comparison.texts_match("the fee is 1.500", "the fee is 1.5") is False
    assert comparison.texts_match("the fee is 1.500", "the fee is 1.500") is False
    assert comparison.texts_match("kwota 1.500,00 zł", "kwota 1500 zł") is True
    assert comparison.texts_match("beløp 1.500,50 kr", "beløp 1500.50 kr") is True
    assert comparison._numbers("1 500,50") == comparison._numbers("1\u00a0500,50") == ("1 500.5",)
    assert comparison.texts_match("kwota 1 500,50 zł", "kwota 1500,50 zł") is False
    # A day greater than 12 makes the dotted date unambiguous. 04.11.2026 does not.
    assert comparison._numbers("dnia 15.11.2026") == ()
    assert comparison.texts_match("dnia 15.11.2026", "on 2026-11-15") is True
    assert comparison.texts_match("dnia 04.11.2026", "on 2026-11-04") is False
    assert comparison.texts_match("4 listopada 2026", "4. november 2026") is True
    assert comparison.texts_match("4 listopada 2026", "5 listopada 2026") is False

    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    report = _report(
        _analysis(red_flags=[_high_flag("The duty is 1.500 zł.")]),
        _analysis(red_flags=[_high_flag("The duty is 1500 zł.")]),
    )
    assert report["high_severity_disagreements"]
    held = avatar.cast_vote(
        {
            "title": "Duty",
            "sections": [{"section_id": "s1", "heading": "Duty", "summary": "Duty."}],
            "model_comparison": report,
        },
        _profile(),
        provider=FakeProvider([json.dumps(_vote("yes"))]),
    )
    assert held.section_votes[0].position == "abstain"


def test_separator_only_differences_still_agree():
    """Apostrophes and a resolved decimal comma still match. Bare 1,500 does not."""
    assert comparison.texts_match("the fee is 1,500", "the fee is 1500") is False
    assert comparison.texts_match("the fee is 1,50", "the fee is 1.5") is True
    assert comparison.texts_match("the fee is 1'500", "the fee is 1500") is True
    assert comparison.texts_match("the fee is 1’500", "the fee is 1500") is True
    assert comparison.texts_match("the fee is 1,500.00", "the fee is 1500") is True
    for grouped in ("1 500 zł", "1\u00a0500 zł", "1\u202f500 kr", "1\u2009500 kr"):
        assert comparison._numbers(grouped) == ("1 500",), grouped
        assert comparison.texts_match(grouped, "1500 zł" if "zł" in grouped else "1500 kr") is False
    assert comparison.texts_match("1 500 zł", "1\u00a0500 zł") is True


def test_lists_dates_and_versions_are_not_plain_numbers(monkeypatch):
    assert comparison._numbers("sections 3, 4 and 5") == ("3", "4", "5")
    assert comparison._numbers("sections 3,4") == ("3.4",)
    assert comparison.texts_match("sections 3, 4 and 5", "sections 5, 4 and 3") is False
    assert comparison.texts_match("sections 3, 4 and 5", "sections 3, 4, and 5") is True
    assert comparison.texts_match("shipped on 2026-11-04", "shipped on 2026-11-05") is False
    assert comparison.texts_match("build v1.2.3", "build v1.2.4") is False
    assert comparison.texts_match("build v1.2.3", "build v1.2.3") is True
    assert comparison.texts_match("build v1.2.3", "sections 1, 2 and 3") is False
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    report = _report(
        _analysis(red_flags=[_high_flag("It cites sections 3, 4 and 5.")]),
        _analysis(red_flags=[_high_flag("It cites sections 5, 4 and 3.")]),
    )
    assert report["high_severity_disagreements"]
    result = avatar.cast_vote(
        {
            "title": "List",
            "sections": [{"section_id": "s1", "heading": "List", "summary": "List."}],
            "model_comparison": report,
        },
        _profile(),
        provider=FakeProvider([json.dumps(_vote("yes"))]),
    )
    assert result.section_votes[0].position == "abstain"
    assert result.section_votes[0].comparison_prior_position == "yes"


def test_format_changes_do_not_create_false_agreement(monkeypatch):
    """Different values, reversed ranges, and shall/shall not still abstain."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    pairs = [
        ("The tax moves from 5 to 10 percent.", "The tax moves from 10 to 5 percent."),
        ("The agency shall publish the names.", "The agency shall not publish the names."),
        ("The fee is 1,500.", "The fee is 1.5."),
        ("The fee is 1.500.", "The fee is 1500."),
        ("The fee is 1.500.", "The fee is 1.5."),
        ("Due 04.11.2026.", "Due 2026-11-04."),
        ("Build v1.2.3.", "Build v1.2.4."),
    ]
    for left, right in pairs:
        report = _report(
            _analysis(red_flags=[_high_flag(left)]),
            _analysis(red_flags=[_high_flag(right)]),
        )
        assert report["high_severity_disagreements"], (left, right)
        result = avatar.cast_vote(
            {
                "title": "Bill",
                "sections": [{"section_id": "s1", "heading": "S", "summary": "S."}],
                "model_comparison": report,
            },
            _profile(),
            provider=FakeProvider([json.dumps(_vote("yes"))]),
        )
        assert result.section_votes[0].position == "abstain", (left, right)
        assert "s1" not in result.would_pass_sections


def _abstains(left: str, right: str) -> None:
    report = _report(
        _analysis(red_flags=[_high_flag(left)]),
        _analysis(red_flags=[_high_flag(right)]),
    )
    assert report["high_severity_disagreements"], (left, right)
    result = avatar.cast_vote(
        {
            "title": "Bill",
            "sections": [{"section_id": "s1", "heading": "S", "summary": "S."}],
            "model_comparison": report,
        },
        _profile(),
        provider=FakeProvider([json.dumps(_vote("yes"))]),
    )
    assert result.section_votes[0].position == "abstain", (left, right)
    assert result.section_votes[0].comparison_prior_position == "yes"
    assert "s1" not in result.would_pass_sections


def test_leading_zero_comma_is_a_decimal_not_thousands(monkeypatch):
    """Critic: 0,500 must not agree with 500 or with 500%."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    assert comparison._numbers("0,500") == ("0.5",)
    assert comparison._numbers("0,500%") == ("0.5%",)
    assert comparison.texts_match("the rate is 0,500", "the rate is 0.5") is True
    assert comparison.texts_match("the rate is 0,500", "the rate is 500") is False
    assert comparison.texts_match("the rate is 0,500%", "the rate is 500%") is False
    _abstains("The rate is 0,500.", "The rate is 500.")
    _abstains("The rate is 0,500%.", "The rate is 500%.")


def test_bare_comma_group_of_three_is_ambiguous(monkeypatch):
    """Critic: bare 1,500 must not agree with 1500. Resolved forms still do."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    assert comparison._numbers("1,500") == ("<ambiguous>",)
    assert comparison._numbers("2,250") == ("<ambiguous>",)
    assert comparison.texts_match("the fee is 1,500", "the fee is 1500") is False
    assert comparison.texts_match("the fee is 1,500", "the fee is 1,500") is False
    assert comparison._numbers("1,500.00") == ("1500",)
    assert comparison._numbers("1.500,00") == ("1500",)
    assert comparison._numbers("1,500,000") == ("1500000",)
    assert comparison._numbers("1.500.000") == ("1500000",)
    assert comparison.texts_match("the fee is 1,500.00", "the fee is 1500") is True
    assert comparison.texts_match("the fee is 1.500,00", "the fee is 1500") is True
    assert comparison.texts_match("the fee is 1,500,000", "the fee is 1500000") is True
    assert comparison.texts_match("the fee is 1.500.000", "the fee is 1500000") is True
    _abstains("The fee is 1,500.", "The fee is 1500.")
    _abstains("The fee is 2,250.", "The fee is 2250.")


def test_dotted_citation_abbreviations_do_not_join_even_with_currency(monkeypatch):
    """Critic: the period in art. must not let zł glue 12 500 into 12500."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    assert comparison.texts_match("art. 12 500 zł kary", "art. 12500 zł kary") is False
    assert comparison._numbers("art. 12 500 zł kary") == ("12 500",)
    _abstains("art. 12 500 zł kary", "art. 12500 zł kary")
    for marker in ("art", "ust", "pkt", "sec", "nr", "lit", "poz", "ledd"):
        for written in (f"{marker}. 12 500 zł", f"{marker}.12 500 zł", f"{marker} 12 500 zł"):
            assert comparison.texts_match(written, "12500 zł") is False, written
            assert comparison._numbers(written) == ("12 500",), written
    assert comparison._numbers("§ 3 500 zł") == ("3 500",)
    assert comparison._numbers("§3 500 zł") == ("3 500",)
    assert comparison.texts_match("§ 3 500 zł", "3500 zł") is False
    assert comparison.texts_match("Art. 12 500 ZŁ", "12500 zł") is False
    assert comparison.texts_match("1 500 zł", "1500 zł") is False


def test_leading_zero_comma_with_a_long_fraction_matches_the_classifier():
    assert comparison._numbers("0,5000") == ("0.5",)
    assert comparison.texts_match("the rate is 0,5000", "the rate is 0.5") is True
    assert comparison.texts_match("the rate is 0,5000", "the rate is 5000") is False


def test_zloty_stays_one_token():
    assert comparison._tokens("zł") == {"zł"}


_CITATION_MARKERS = (
    "art",
    "kap",
    "rozdz",
    "no",
    "No",
    "par",
    "Abs",
    "tit",
    "§",
    "ledd",
    # Made-up abbreviations, one through five letters. The split does not
    # depend on the word being on a list.
    "q",
    "zx",
    "bqq",
    "wxyz",
    "abcde",
)


@pytest.mark.parametrize("marker", _CITATION_MARKERS)
@pytest.mark.parametrize("period", ("", "."))
def test_any_abbreviation_space_group_abstains_against_the_joined_form(monkeypatch, marker, period):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    left = f"{marker}{period} 12 500 zł"
    right = f"{marker}{period} 12500 zł"
    assert comparison._numbers(left) == ("12 500",)
    assert comparison._numbers(right) == ("12500",)
    assert comparison.texts_match(left, right) is False
    _abstains(left, right)


@pytest.mark.parametrize("dash", ("-", "\u2013"))
def test_hyphenated_range_abstains_against_the_joined_form(monkeypatch, dash):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    left = f"art. 12{dash}14 500 zł"
    right = f"art. 12{dash}14500 zł"
    assert comparison._numbers(left) == ("12", "14 500")
    assert comparison._numbers(right) == ("12", "14500")
    assert comparison.texts_match(left, right) is False
    _abstains(left, right)
    assert comparison.texts_match(left, f"art. 12{dash}14 500 zł") is True


@pytest.mark.parametrize("gap", ("\n", "\r\n", "\t"))
def test_line_broken_groups_abstain_against_the_joined_form(monkeypatch, gap):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    left = f"12{gap}500 zł"
    assert comparison._numbers(left) == ("12 500",)
    assert comparison.texts_match(left, "12500 zł") is False
    _abstains(left, "12500 zł")


@pytest.mark.parametrize(
    "marked,bare",
    (
        ("§ 1 000 kr", "1 000 kr"),
        ("§§ 1 000", "1 000"),
        ("§1 000", "1 000"),
        ("№ 1 000", "1 000"),
        ("# 1 000", "1 000"),
        ("(1 000", "1 000"),
        ("я 1 000", "1 000"),
    ),
)
def test_signs_outside_a_number_abstain_against_the_bare_amount(monkeypatch, marked, bare):
    """A sign the old token pattern dropped must not disappear into a yes."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    assert comparison._high_claims_agree(marked, bare) is False
    _abstains(marked, bare)


@pytest.mark.parametrize(
    "left,right",
    (
        ("The fee is 1 000 kr.", "The fee is 1 000 kr"),
        ("The fee is 1 000 kr!", "The fee is 1 000 kr?"),
        ('The fee is "1 000 kr"', "The fee is 1 000 kr"),
        ("The fee is ‘1 000 kr’", "The fee is 1 000 kr"),
        ("The well-known fee is 1 000 kr.", "The well known fee is 1 000 kr"),
        ("The fee is 1 000 kr;", "The fee is 1 000 kr:"),
    ),
)
def test_sentence_punctuation_alone_still_matches(left, right):
    assert comparison.texts_match(left, right) is True
    assert comparison._high_claims_agree(left, right) is True


def test_zero_width_space_is_not_a_grouping_gap():
    assert comparison._numbers("12\u200b500") == ("12", "500")
    assert comparison.texts_match("12\u200b500 zł", "12 500 zł") is False
    assert comparison.texts_match("12\u200b500 zł", "12500 zł") is False


@pytest.mark.parametrize(
    "gap",
    (" ", "\u00a0", "\u202f", "\u2009", "\n", "\t", "\r\n"),
)
def test_identical_space_groups_match(gap):
    grouped = f"12{gap}500 zł"
    assert comparison._numbers(grouped) == ("12 500",)
    assert comparison.texts_match(grouped, "12 500 zł") is True
    assert comparison.texts_match(grouped, "12500 zł") is False
    assert comparison.texts_match("12 500 zł", "1 2500 zł") is False
    assert comparison.texts_match("12 500 zł", "012 500 zł") is False


def test_space_groups_do_not_merge_citations(monkeypatch):
    """A space-grouped amount is not the joined integer, with or without a unit."""
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    assert comparison._numbers("Articles 1 500") == ("1 500",)
    assert comparison._numbers("§ 3 500") == ("3 500",)
    assert comparison._numbers("1 500 zł") == ("1 500",)
    assert comparison._numbers("1 500 kr") == ("1 500",)
    assert comparison.texts_match("Articles 1 500", "Articles 1500") is False
    assert comparison.texts_match("§ 3 500", "§ 3500") is False
    assert comparison.texts_match("1 500 zł", "1500 zł") is False
    assert comparison.texts_match("1 500 kr", "1500 kr") is False
    _abstains("See Articles 1 500.", "See Articles 1500.")
    _abstains("See § 3 500.", "See § 3500.")
    _abstains("The fee is 1 500 zł.", "The fee is 1500 zł.")


def test_same_vendor_family_is_refused(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o")
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "gpt-4o-mini")
    with pytest.raises(llm.LLMConfigError, match="not an independent check"):
        comparison.analyze_for_api("Sec. 101. A short bill about taxes and rates.")


def test_same_provider_and_model_is_refused(monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "claude-sonnet-4-6")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(llm.LLMConfigError, match="not an independent check"):
        comparison.analyze_for_api("Sec. 101. " + SHALL)


def test_analyze_api_missing_comparison_key_is_explicit_500(client, monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "llama3.1:8b")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    response = client.post("/api/analyze", files={"text": (None, "Section 1. " * 20)})
    assert response.status_code == 500
    assert "OPENAI_API_KEY" in response.json()["detail"]
