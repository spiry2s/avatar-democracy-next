"""
Fixes from the live run against real bills.

No network and no API key. Model calls are scripted. The fixtures under
tests/fixtures/ are the bill excerpts from that run.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from fastapi.testclient import TestClient

from backend import avatar, comparison, config, grounding, llm, summarizer
from backend.profile import CitizenProfile
import main as main_mod


FIXTURES = Path(__file__).parent / "fixtures"
QUOTE = "The Secretary shall publish subsidy recipients in this annual report."


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
        max_tokens: int | None = None,
        data_blocks: list[llm.DataBlock] | None = None,
    ) -> str:
        del data_blocks
        self.calls.append({"user": user, "system": system, "max_tokens": max_tokens})
        if not self._responses:
            raise AssertionError("FakeProvider ran out of scripted responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeHttp:
    posts: list[dict[str, Any]] = []
    response: httpx.Response | None = None

    def __init__(self, **kwargs: Any):
        del kwargs

    def __enter__(self) -> "FakeHttp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, json: Any = None, headers: Any = None) -> httpx.Response:
        FakeHttp.posts.append({"url": url, "json": json, "headers": headers})
        assert FakeHttp.response is not None
        return FakeHttp.response


def _blocks(text: str) -> list[tuple[str, str]]:
    parts = text.split("\n===")
    blocks: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        chunk = part if index == 0 else "===" + part
        if "===" not in chunk:
            continue
        start = chunk.find("===")
        body = chunk[start:]
        header, _, rest = body.partition("\n")
        blocks.append((header, rest))
    return blocks


def _quotes(body: str) -> list[tuple[str, str]]:
    import re

    pieces = re.split(r"(?m)^--- section_id='([^']+)'[^\n]*\n", body)
    found: list[tuple[str, str]] = []
    index = 1
    while index + 1 < len(pieces):
        text = pieces[index + 1].split("\nNOTE:", 1)[0].split("\n\n", 1)[0].strip()
        found.append((pieces[index], text))
        index += 2
    return found


def _analysis(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Plain title",
        "plain_summary": "It does a thing.",
        "sections": [
            {
                "section_id": "s1",
                "heading": "Search",
                "summary": "Allows a search.",
            }
        ],
        "beneficiaries": {"benefits": [], "costs": [], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {},
    }
    base.update(overrides)
    return base


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
            }
        ],
        "recommendation": "Vote yes.",
        "would_pass_sections": ["s1"],
        "key_tension": "",
    }


def _profile() -> CitizenProfile:
    return CitizenProfile(values="Protect privacy.", compass={"liberty": 0.4})


@pytest.fixture
def client():
    return TestClient(main_mod.app, base_url="http://127.0.0.1:8000")


def _configure_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "deepseek-flash")
    monkeypatch.setattr(config, "COMPARISON_VOTE_POLICY", "abstain")
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-sonnet-4-6")


def _ground(quote: str, source: str, cited: str) -> dict[str, Any]:
    return grounding.accept_quote(
        quote,
        source,
        cited_section=cited,
        spans=grounding.bill_section_spans(source),
    )


# ── output budget and cut-off replies ─────────────────────────────────────────


def test_output_budget_defaults_and_is_what_analysis_sends(monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_TOKENS", 32000)
    monkeypatch.setattr(config, "COMPARISON_MAX_TOKENS", 48000)
    assert llm.output_budget(None) == 32000
    fake = FakeProvider([json.dumps(_analysis())])
    summarizer.analyze_bill("A bill about farms. " * 5, provider=fake)
    assert fake.calls[0]["max_tokens"] == 32000

    _configure_comparison(monkeypatch)
    primary = FakeProvider([json.dumps(_analysis())])
    other = FakeProvider([json.dumps(_analysis())], model="deepseek-flash")
    other.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    comparison.analyze_for_api("A bill about farms. " * 5)
    assert primary.calls[0]["max_tokens"] == 32000
    assert other.calls[0]["max_tokens"] == 48000


def _openai_payload(content: str, finish_reason: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return body


def test_length_stop_is_never_parsed_as_json(monkeypatch):
    monkeypatch.setattr(llm.httpx, "Client", FakeHttp)
    FakeHttp.posts = []
    FakeHttp.response = httpx.Response(
        200,
        json=_openai_payload(
            '{"ok": true}',
            "length",
            {
                "prompt_tokens": 11,
                "completion_tokens": 8000,
                "completion_tokens_details": {"reasoning_tokens": 7004},
            },
        ),
        request=httpx.Request("POST", "http://example.test/v1/chat/completions"),
    )
    provider = llm.OpenAICompatibleProvider(
        model="deepseek-flash",
        api_key="sk-test",
        base_url="https://api.deepseek.com",
    )
    with pytest.raises(llm.LLMTruncatedError, match="length") as exc:
        provider.complete_json(user="bill", system="json", max_tokens=8000)
    assert not isinstance(exc.value, ValueError)
    assert '{"ok": true}' not in str(exc.value)
    assert provider.usage_log[-1].input_tokens == 11
    assert provider.usage_log[-1].output_tokens == 8000
    assert provider.usage_log[-1].reasoning_tokens == 7004
    assert "sk-test" not in str(provider.usage_log[-1])


@pytest.mark.parametrize("content", ["", "   \n\t", "   "])
def test_empty_openai_content_is_a_truncation_error(monkeypatch, content: str):
    monkeypatch.setattr(llm.httpx, "Client", FakeHttp)
    FakeHttp.response = httpx.Response(
        200,
        json=_openai_payload(content, "stop"),
        request=httpx.Request("POST", "http://example.test/v1/chat/completions"),
    )
    provider = llm.OpenAICompatibleProvider(
        model="deepseek-flash", api_key="sk-test", base_url="http://example.test/v1"
    )
    with pytest.raises(llm.LLMTruncatedError, match="not parsed"):
        provider.complete(user="bill")


def test_anthropic_max_tokens_stop_is_not_parsed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    message = type("Msg", (), {})()
    message.content = [type("Block", (), {"text": '{"ok": true}'})()]
    message.stop_reason = "max_tokens"
    message.usage = type("Usage", (), {"input_tokens": 5, "output_tokens": 9, "reasoning_tokens": 2})()

    class FakeStream:
        def __enter__(self) -> "FakeStream":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def get_final_message(self) -> Any:
            return message

    class FakeMessagesWithBody:
        def create(self, **kwargs: Any) -> Any:
            raise AssertionError("non-streaming messages.create was called")

        def stream(self, **kwargs: Any) -> FakeStream:
            del kwargs
            return FakeStream()

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            del api_key
            self.messages = FakeMessagesWithBody()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    calls = {"loads": 0}

    def counting_loads(*_args: Any, **_kwargs: Any) -> Any:
        calls["loads"] += 1
        raise AssertionError("truncated Anthropic text was parsed")

    monkeypatch.setattr(llm.json, "loads", counting_loads)
    provider = llm.AnthropicProvider(model="claude-sonnet-4-6")
    with pytest.raises(llm.LLMTruncatedError, match="max_tokens"):
        provider.complete_json(user="bill")
    assert calls["loads"] == 0
    assert provider.usage_log[-1].reasoning_tokens == 2


def test_anthropic_empty_text_is_a_truncation_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    message = type("Msg", (), {})()
    message.content = [type("Block", (), {"text": "  "})()]
    message.stop_reason = "end_turn"
    message.usage = None

    class FakeStream:
        def __enter__(self) -> "FakeStream":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def get_final_message(self) -> Any:
            return message

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            del api_key
            self.messages = type(
                "M",
                (),
                {
                    "create": lambda self, **kwargs: (_ for _ in ()).throw(
                        AssertionError("non-streaming messages.create was called")
                    ),
                    "stream": lambda self, **kwargs: FakeStream(),
                },
            )()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    provider = llm.AnthropicProvider(model="m")
    with pytest.raises(llm.LLMTruncatedError, match="not parsed"):
        provider.complete(user="bill")


def test_truncated_analysis_is_not_retried_as_a_schema_error():
    fake = FakeProvider([llm.LLMTruncatedError("Model output was cut off (finish_reason='length').")])
    with pytest.raises(llm.LLMTruncatedError, match="length"):
        summarizer.analyze_bill("A bill about farms. " * 5, provider=fake)
    assert len(fake.calls) == 1


def test_analyze_endpoint_reports_truncation_without_calling_it_unparseable(client, monkeypatch):
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "")

    def factory(role: str = "primary") -> FakeProvider:
        del role
        return FakeProvider(
            [llm.LLMTruncatedError("Model output was cut off (finish_reason='length'). The reply was not parsed as JSON.")]
        )

    monkeypatch.setattr(llm, "get_provider", factory)
    response = client.post("/api/analyze", files={"text": (None, "Section 1. " * 20)})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "cut off" in detail
    assert "unparseable" not in detail.lower()


# ── comparison failure keeps the primary and never becomes a yes ─────────────


def test_comparison_calls_overlap_and_keep_independent_errors(monkeypatch):
    _configure_comparison(monkeypatch)
    barrier = threading.Barrier(2, timeout=2)
    primary = FakeProvider([json.dumps(_analysis(title="Primary title"))])
    other = FakeProvider([json.dumps(_analysis(title="Other title"))], model="deepseek-flash")
    other.provider_id = "openai"

    class Waiting(FakeProvider):
        def complete(self, **kwargs: Any) -> str:
            barrier.wait()
            return FakeProvider.complete(self, **kwargs)

    primary_waiting = Waiting([json.dumps(_analysis(title="Primary title"))])
    other_waiting = Waiting([json.dumps(_analysis())], model="deepseek-flash")
    other_waiting.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other_waiting if role == "comparison" else primary_waiting

    monkeypatch.setattr(llm, "get_provider", factory)
    result = comparison.analyze_for_api("A bill about farms. " * 5)
    assert result["title"] == "Primary title"
    assert result["model_comparison"]["status"] == "available"
    assert len(primary_waiting.calls) == 1
    assert len(other_waiting.calls) == 1
    del primary, other


_GROUNDED_SENTENCE = "The agency may search stored mail without a warrant issued by any court."
_GROUNDED_BILL = "Sec. 101. Search.\n\n" + _GROUNDED_SENTENCE + "\n"


def _grounded_analysis() -> dict[str, Any]:
    return _analysis(
        title="Kept primary",
        sections=[{
            "section_id": "Sec. 101",
            "heading": "Search",
            "summary": "Allows a search of stored mail without a warrant.",
            "quote": _GROUNDED_SENTENCE,
            "significance": "high",
        }],
    )


def _yes_vote() -> dict[str, Any]:
    return {
        "section_votes": [{
            "section_id": "Sec. 101",
            "heading": "Search",
            "position": "yes",
            "basis": "values",
            "reasoning": "Privacy.",
            "confidence": "high",
        }],
        "recommendation": "Vote yes.",
        "would_pass_sections": ["Sec. 101"],
        "key_tension": "",
    }


def test_comparison_exception_keeps_primary_and_abstains(monkeypatch):
    _configure_comparison(monkeypatch)
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    primary = FakeProvider([json.dumps(_grounded_analysis())])

    class UsageThenFail(FakeProvider):
        def complete(self, **kwargs: Any) -> str:
            self.note_usage(llm.TokenUsage(input_tokens=20, output_tokens=5, reasoning_tokens=4))
            self.calls.append(kwargs)
            raise TimeoutError("comparison timed out")

    other = UsageThenFail([], model="deepseek-flash")
    other.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    result = comparison.analyze_for_api(_GROUNDED_BILL)
    assert result["title"] == "Kept primary"
    assert result["sections"][0]["grounded"] is True
    report = result["model_comparison"]
    assert report["status"] == "unavailable"
    assert report["enabled"] is True
    assert "comparison unavailable" in report["note"]
    assert "timed out" in report["unavailable_reason"]
    assert report["high_severity_disagreements"] == []
    assert result["token_usage"][-1]["role"] == "comparison"
    assert result["token_usage"][-1]["reasoning_tokens"] == 4
    assert "sk-" not in json.dumps(report)

    vote = avatar.cast_vote(result, _profile(), provider=FakeProvider([json.dumps(_yes_vote())]))
    assert vote.section_votes[0].position == "abstain"
    assert vote.section_votes[0].comparison_abstained is True
    assert "comparison unavailable" in vote.section_votes[0].comparison_note
    assert "Sec. 101" not in vote.would_pass_sections
    assert vote.section_votes[0].position != "yes"


def test_comparison_truncation_and_review_flag_never_an_unflagged_yes(monkeypatch):
    _configure_comparison(monkeypatch)
    monkeypatch.setattr(config, "COMPARISON_VOTE_POLICY", "review")
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    primary = FakeProvider([json.dumps(_grounded_analysis())])
    other = FakeProvider(
        [llm.LLMTruncatedError("Model output was cut off (finish_reason='length').")],
        model="deepseek-flash",
    )
    other.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    result = comparison.analyze_for_api(_GROUNDED_BILL)
    assert result["title"] == "Kept primary"
    assert result["sections"][0]["grounded"] is True
    assert result["model_comparison"]["status"] == "unavailable"
    assert "length" in result["model_comparison"]["unavailable_reason"]

    vote = avatar.cast_vote(result, _profile(), provider=FakeProvider([json.dumps(_yes_vote())]))
    section = vote.section_votes[0]
    assert section.needs_human_review is True
    assert section.position == "yes"
    assert "comparison unavailable" in section.comparison_note
    assert "human review" in section.comparison_note
    # A failed check must not become an unflagged yes.
    assert not (section.position == "yes" and not section.needs_human_review)


def test_primary_error_is_not_replaced_by_the_comparison_error(monkeypatch):
    _configure_comparison(monkeypatch)
    primary = FakeProvider([RuntimeError("primary down")])
    other = FakeProvider([TimeoutError("comparison timed out")], model="deepseek-flash")
    other.provider_id = "openai"

    def factory(role: str = "primary") -> FakeProvider:
        return other if role == "comparison" else primary

    monkeypatch.setattr(llm, "get_provider", factory)
    with pytest.raises(RuntimeError, match="primary down"):
        comparison.analyze_for_api("A bill about farms. " * 5)


def test_unavailable_review_abstains_when_primary_has_a_high_flag():
    """A missing comparison must not keep a yes on a section the primary already flagged high."""
    report = {
        "enabled": True,
        "status": "unavailable",
        "unavailable_reason": "TimeoutError: comparison timed out",
        "policy": "review",
        "high_severity_disagreements": [],
    }
    analysis = {
        "title": "Bill",
        "sections": [
            {"section_id": "s1", "heading": "Search"},
            {"section_id": "s2", "heading": "Parks"},
        ],
        "red_flags": [{
            "type": "vague_language",
            "section": "s1",
            "severity": "high",
            "description": "The duty is undefined.",
            "grounded": True,
        }],
        "model_comparison": report,
    }
    votes = [
        avatar.SectionVote("s1", "Search", "yes", "values", "Because.", "high"),
        avatar.SectionVote("s2", "Parks", "yes", "values", "Fine.", "high"),
    ]
    abstained = comparison.apply_vote_policy(votes, analysis)
    assert votes[0].position == "abstain"
    assert votes[0].comparison_abstained is True
    assert votes[0].needs_human_review is False
    assert "high-severity" in votes[0].comparison_note
    assert "s1" in abstained
    assert votes[1].position == "yes"
    assert votes[1].needs_human_review is True
    assert votes[1].comparison_abstained is False
    assert "s2" not in abstained


def test_models_disagree_note_names_the_reason():
    report = comparison.build_report(
        _analysis(red_flags=[{
            "type": "vague_language",
            "section": "s1",
            "description": "The duty is shall.",
            "severity": "high",
        }]),
        _analysis(),
        primary_info={"provider": "anthropic", "model": "claude-sonnet-4-6"},
        comparison_info={"provider": "openai", "model": "deepseek-flash"},
        policy="abstain",
    )
    votes = [
        avatar.SectionVote("s1", "Search", "yes", "values", "Because.", "high"),
    ]
    comparison.apply_vote_policy(votes, {"model_comparison": report})
    assert votes[0].position == "abstain"
    assert "models disagree" in votes[0].comparison_note


def test_unavailable_reason_redacts_a_key():
    reason = comparison._public_failure(RuntimeError("bad key sk-ant-abcdefghijklmnopqrstuvwxyz"))
    assert "sk-ant-abc" not in reason
    assert "[redacted]" in reason


# ── whitespace folding, mapped back onto the original characters ─────────────


def test_whitespace_collapse_maps_back_to_the_original_span():
    source = "hello   world"
    folded, ends = grounding.fold_for_match(source)
    assert folded == "hello world"
    start = folded.find("hello world")
    assert source[ends[start] : ends[start + len("hello world")]] == "hello   world"

    wrapped = "hello\n    world"
    folded, ends = grounding.fold_for_match(wrapped)
    assert folded == "hello world"
    start = folded.find("hello world")
    span = wrapped[ends[start] : ends[start + len("hello world")]]
    assert span == wrapped
    assert "\n" in span


def test_unicode_spaces_collapse_and_zero_width_space_does_not():
    source = "The\u00a0\u3000Secretary shall publish subsidy recipients in this annual report."
    quote = "The Secretary shall publish subsidy recipients in this annual report."
    located = grounding.accept_quote(quote, source)
    assert located["grounded"] is True
    span = source[located["quote_start"] : located["quote_end"]]
    assert "\u00a0" in span and "\u3000" in span
    assert span != quote

    hidden = "The fee is 12\u200b500 zł for each registered recipient in this act."
    assert grounding.accept_quote(
        "The fee is 12500 zł for each registered recipient in this act.", hidden
    )["grounded"] is False
    assert grounding.accept_quote(
        "The fee is 12 500 zł for each registered recipient in this act.", hidden
    )["grounded"] is False
    assert grounding.fold_for_match("12\u200b500")[0] == "12\u200b500"


def test_whitespace_fold_does_not_invent_words_or_drop_negation():
    bill = "The Secretary shall not publish subsidy recipients in this annual report."
    assert grounding.accept_quote(
        "The Secretary shallnot publish subsidy recipients in this annual report.", bill
    )["grounded"] is False
    assert grounding.accept_quote(
        "The Secretary shall not publish subsidy recipients in this annual report.",
        "The Secretary shall\nnot publish subsidy recipients in this annual report.",
    )["grounded"] is True
    assert grounding.accept_quote("shall", bill)["grounding_reason"] == "quote_too_short"
    assert grounding.fold_for_match("sh\nall")[0] != "shall"
    assert grounding.accept_quote("12500 zł for the registered recipient of this subsidy.", "12 500 zł for the registered recipient of this subsidy.")[
        "grounded"
    ] is False


def test_line_hyphen_joins_only_a_lowercase_syllable_and_maps_back():
    source = "minister właściwy do spraw rozwoju regional-\nnego, na zasadach określonych"
    quote = "minister właściwy do spraw rozwoju regionalnego, na zasadach określonych"
    located = grounding.accept_quote(quote, source)
    assert located["grounded"] is True
    span = source[located["quote_start"] : located["quote_end"]]
    assert span == source
    assert "regional-\nnego" in span
    indented = source.replace("regional-\nnego", "regional-\n    nego")
    indented_hit = grounding.accept_quote(quote, indented)
    assert indented_hit["grounded"] is True
    assert "regional-\n    nego" in indented[indented_hit["quote_start"] : indented_hit["quote_end"]]
    spaced = source.replace("regional-\nnego", "regional- \nnego")
    assert grounding.accept_quote(quote, spaced)["grounded"] is False

    soft = "The Secretary shall publish sub\u00adsidy recipients to the public each year."
    soft_quote = "The Secretary shall publish subsidy recipients to the public each year."
    located = grounding.accept_quote(soft_quote, soft)
    assert located["grounded"] is True
    assert "\u00ad" in soft[located["quote_start"] : located["quote_end"]]

    assert grounding.accept_quote(
        "catdog walks into the statute and stays there today.",
        "cat-\ndog walks into the statute and stays there today.",
    )["grounded"] is False
    assert grounding.accept_quote(
        "The stateowned carrier shall publish subsidy recipients yearly.",
        "The state-\nowned carrier shall publish subsidy recipients yearly.",
    )["grounded"] is False
    assert grounding.accept_quote(
        "The stateowned carrier shall publish subsidy recipients yearly.",
        "The state\u2014\nowned carrier shall publish subsidy recipients yearly.",
    )["grounded"] is False
    assert grounding.accept_quote(
        "Regionalnego dokumentu nie ma w tej ustawie o fundacji przemysłu.",
        "Regional-\nnego dokumentu nie ma w tej ustawie o fundacji przemysłu.",
    )["grounded"] is False
    # A hyphen kept in the quote is not the same as a deleted one.
    assert grounding.accept_quote(
        "The state-owned carrier shall publish subsidy recipients yearly.",
        "The state-\nowned carrier shall publish subsidy recipients yearly.",
    )["grounded"] is False


def test_a_heading_between_words_is_not_collapsed_away():
    source = (
        "SEC. 1. Alpha.\n\n"
        "alpha rule about publishing subsidy recipients in this act.\n"
        "SEC. 2. Beta.\n\n"
        "beta rule about publishing subsidy recipients in this act.\n"
    )
    crossed = (
        "alpha rule about publishing subsidy recipients in this act. "
        "SEC. 2. Beta. "
        "beta rule about publishing subsidy recipients in this act."
    )
    assert _ground(crossed, source, "Sec. 1")["grounded"] is False
    assert _ground(
        "alpha rule about publishing subsidy recipients in this act.", source, "Sec. 1"
    )["grounded"] is True
    assert _ground(
        "alpha rule about publishing subsidy recipients in this act.", source, "Sec. 2"
    )["grounding_reason"] == "quote_not_in_section"


# ── section headings and the real fixtures ────────────────────────────────────


def test_fixture_files_keep_their_credits():
    readme = (FIXTURES / "README.txt").read_text(encoding="utf-8")
    assert "NLOD 2.0" in readme
    assert "Lovdata" in readme
    lovdata = (FIXTURES / "lovdata_helligdagsfredloven_excerpt.txt").read_text(encoding="utf-8")
    assert lovdata.startswith("Source: Lovdata (lovdata.no), NLOD 2.0\n")
    sejm = (FIXTURES / "sejm_DU_2024_1823_excerpt.txt").read_text(encoding="utf-8")
    assert sejm.startswith("Source: Dziennik Ustaw 2024 poz. 1823, via api.sejm.gov.pl\n")


def test_govinfo_quotes_ground_in_the_cited_section_and_not_the_other():
    text = (FIXTURES / "govinfo_hr7521_excerpt.txt").read_text(encoding="utf-8")
    blocks = _blocks(text)
    source = blocks[0][1]
    quotes = _quotes(blocks[1][1])
    assert [section for section, _quote in quotes] == ["Sec. 1", "Sec. 2(a)", "Sec. 2(b)"]
    for section, quote in quotes:
        located = _ground(quote, source, section)
        assert located["grounded"] is True, section
        span = source[located["quote_start"] : located["quote_end"]]
        assert "\n" in span
        assert grounding.fold_for_match(quote)[0] in grounding.fold_for_match(span)[0]
    short_title = quotes[0][1]
    wrong = _ground(short_title, source, "Sec. 2")
    assert wrong["grounded"] is False
    assert wrong["grounding_reason"] == "quote_not_in_section"


def test_lovdata_quotes_ground_in_the_paragraph_and_not_another():
    text = (FIXTURES / "lovdata_helligdagsfredloven_excerpt.txt").read_text(encoding="utf-8")
    blocks = _blocks(text)
    source = blocks[0][1]
    quotes = _quotes(blocks[1][1])
    assert quotes
    for section, quote in quotes:
        located = _ground(quote, source, section)
        assert located["grounded"] is True, (section, located["grounding_reason"])
        span = source[located["quote_start"] : located["quote_end"]]
        assert grounding.fold_for_match(quote.strip())[0] in grounding.fold_for_match(span)[0]
    paragraph_two = next(quote for section, quote in quotes if section == "§ 2")
    wrong = _ground(paragraph_two, source, "§ 5")
    assert wrong["grounded"] is False
    assert wrong["grounding_reason"] == "quote_not_in_section"
    missing = _ground(paragraph_two, source, "§ 99")
    assert missing["grounding_reason"] == "section_unmatched"


def test_polish_pdf_and_html_quotes_stay_inside_the_cited_article():
    text = (FIXTURES / "sejm_DU_2024_1823_excerpt.txt").read_text(encoding="utf-8")
    blocks = _blocks(text)
    pdf, html = blocks[0][1], blocks[1][1]
    article_one = (
        "Z dniem wejścia w życie niniejszej ustawy otwiera się likwidację "
        "Fundacji Platforma Przemysłu Przyszłości, zwanej dalej „Platformą”"
    )
    for source in (pdf, html):
        hit = _ground(article_one, source, "Art. 1")
        assert hit["grounded"] is True, hit["grounding_reason"]
        wrong = _ground(article_one, source, "Art. 2")
        assert wrong["grounded"] is False
        assert wrong["grounding_reason"] == "quote_not_in_section"

    hyphenated = (
        "właściwy do spraw rozwoju regionalnego, na zasadach określonych "
        "w ustawie z dnia 3 marca 2000"
    )
    hit = _ground(hyphenated, pdf, "Art. 2")
    assert hit["grounded"] is True
    assert "regional-\nnego" in pdf[hit["quote_start"] : hit["quote_end"]]
    assert _ground(hyphenated, pdf, "Art. 1")["grounded"] is False

    december = "Z dniem 31 grudnia 2024 r. likwiduje się Platformę."
    assert _ground(december, html, "Art. 1 ust. 2")["grounded"] is True
    assert _ground(december, html, "Art. 2 ust. 2")["grounding_reason"] == "quote_not_in_section"
    # A wrapped lowercase cross-reference is not an Art. 231 heading.
    assert _ground(december, html, "Art. 231")["grounding_reason"] == "section_unmatched"
    assert all(span["key"][0] != ("art", "231") for span in grounding.bill_section_spans(html))


def test_norwegian_and_polish_heading_forms_and_a_heading_inside_a_quote():
    source = (
        "§ 1. Formål med loven.\n"
        "Denne loven skal verne gudstjenester og den offentlige ro på helligdager i hele riket.\n"
        "§ 1a. Virkeområde for reglene.\n"
        "Loven gjelder på norsk territorium og for skip i norsk farvann hele året uten unntak.\n"
        "§§ 4 til 6.\n"
        "Disse paragrafene gjelder bare når departementet har bestemt det uttrykkelig i forskrift.\n"
        "Art. 12a. Przepisy wprowadzające określają dzień wejścia w życie zmian wskazanych w tym artykule.\n"
        "ust. 2. Minister ogłasza tekst jednolity w dzienniku urzędowym w terminie miesiąca od ogłoszenia.\n"
        "pkt 3. Ogłoszenie obejmuje także wykaz przepisów przejściowych i dostosowujących dla pracowników.\n"
    )
    purpose = "Denne loven skal verne gudstjenester og den offentlige ro på helligdager i hele riket."
    scope = "Loven gjelder på norsk territorium og for skip i norsk farvann hele året uten unntak."
    plural = "Disse paragrafene gjelder bare når departementet har bestemt det uttrykkelig i forskrift."
    point = "Ogłoszenie obejmuje także wykaz przepisów przejściowych i dostosowujących dla pracowników."
    assert _ground(purpose, source, "§ 1")["grounded"] is True
    assert _ground(purpose, source, "§ 1a")["grounding_reason"] == "quote_not_in_section"
    assert _ground(scope, source, "§ 1a")["grounded"] is True
    assert _ground(scope, source, "§ 1")["grounding_reason"] == "quote_not_in_section"
    assert _ground(plural, source, "§§ 4")["grounded"] is True
    assert _ground(plural, source, "§ 4")["grounding_reason"] == "section_unmatched"
    assert _ground(point, source, "Art. 12a")["grounded"] is True
    assert _ground(point, source, "ust. 2")["grounded"] is True
    assert _ground(point, source, "pkt 3")["grounded"] is True
    assert _ground(point, source, "Art. 1")["grounding_reason"] == "section_unmatched"
    intro = "Przepisy wprowadzające określają dzień wejścia w życie zmian wskazanych w tym artykule."
    assert _ground(intro, source, "pkt 3")["grounding_reason"] == "quote_not_in_section"

    crossed = purpose + "\n§ 1a. Virkeområde for reglene.\n" + scope
    assert _ground(crossed, source, "§ 1")["grounded"] is False
    assert _ground(crossed, source, "§ 1a")["grounded"] is False


def test_abstain_reason_names_quote_not_found_or_section_mismatch(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    bill = "SEC. 101. Duty.\n\nThe Secretary shall publish subsidy recipients.\n"
    missing = {
        "title": "T",
        "plain_summary": "S",
        "plain_summary_quote": "not in the bill at all, and long enough to be a quote.",
        "sections": [{
            "section_id": "Sec. 101",
            "heading": "Duty",
            "summary": "Missing.",
            "quote": "This quote was never written into the bill text at all.",
        }],
        "beneficiaries": {"benefits": [], "costs": [], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {},
    }
    result = summarizer.analyze_bill(bill, provider=FakeProvider([json.dumps(missing)]))
    assert result.sections[0]["grounding_reason"] == "quote_not_in_source"
    reason = grounding.section_block_reason(result.to_dict(), "Sec. 101")
    assert "quote not found" in reason
    vote = avatar.cast_vote(result.to_dict(), _profile(), provider=FakeProvider([]))
    assert vote.section_votes[0].position == "abstain"
    assert "quote not found" in vote.section_votes[0].blocked_reason
    assert "quote not found" in vote.recommendation

    mismatched = dict(missing)
    mismatched["sections"] = [{
        "section_id": "Sec. 999",
        "heading": "Missing",
        "summary": "Wrong section.",
        "quote": "The Secretary shall publish subsidy recipients.",
    }]
    result = summarizer.analyze_bill(bill, provider=FakeProvider([json.dumps(mismatched)]))
    assert result.sections[0]["grounding_reason"] == "section_unmatched"
    reason = grounding.section_block_reason(result.to_dict(), "Sec. 999")
    assert "section mismatch" in reason
    prompted = avatar._build_bill_block(result.to_dict())
    assert "section mismatch" in prompted
    assert "quote not found" in prompted


# ── health and a taken port ───────────────────────────────────────────────────


def test_health_names_the_comparison_without_secrets(client, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "COMPARISON_PROVIDER", "openai")
    monkeypatch.setattr(config, "COMPARISON_MODEL", "deepseek-flash")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = client.get("/api/health").json()
    assert body["comparison_configured"] is True
    assert body["comparison_provider"] == "openai"
    assert body["comparison_model"] == "deepseek-flash"
    assert body["comparison_key_configured"] is True
    rendered = json.dumps(body)
    assert "sk-secret-value" not in rendered
    assert "api.deepseek.com" not in rendered


def test_taken_port_exits_before_the_starting_banner(monkeypatch, capsys):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", str(port))

    def fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("uvicorn started after a port clash")

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fail_if_called)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Starting Bill Summarizer" not in captured.out
    assert "Starting Bill Summarizer" not in captured.err
    assert "PORT" in captured.err
    assert str(port) in captured.err
    sock.close()

    main_mod.ensure_port_available("127.0.0.1", 0)


# ── regressions from the second live run ──────────────────────────────────────


_NO_SECTION_6 = (
    "Den som forsettlig eller uaktsomt overtrer §§ 3 til 5 i denne lov, "
    "eller bestemmelser gitt i medhold av disse paragrafene, straffes med bøter."
)


def test_wrapped_paragraph_cross_reference_does_not_split_the_section():
    """§ 6's quote contains '§§ 3 til 5'. A wrap must not open a new heading."""
    text = (FIXTURES / "lovdata_helligdagsfredloven_excerpt.txt").read_text(encoding="utf-8")
    source = _blocks(text)[0][1].rstrip() + (
        "\n§ 6.\nStraff\n"
        "Den som forsettlig eller uaktsomt overtrer\n"
        "§§ 3 til 5 i denne lov, eller bestemmelser gitt i medhold av disse paragrafene, straffes med bøter.\n"
    )
    spans = grounding.bill_section_spans(source)
    assert (("paragraphs", "3"),) not in [span["key"] for span in spans]
    assert (("paragraph", "3"),) in [span["key"] for span in spans]
    located = _ground(_NO_SECTION_6, source, "§ 6")
    assert located["grounded"] is True, located["grounding_reason"]
    span = source[located["quote_start"] : located["quote_end"]]
    assert "§§ 3 til 5" in span
    assert _ground(_NO_SECTION_6, source, "§ 3")["grounding_reason"] == "quote_not_in_section"
    # An inline cross-reference in an existing fixture section still grounds there.
    inline = (
        "På helligdag fra kl 00 til kl 24 samt påske-, pinse- og julaften etter kl 16 "
        "skal det være helligdagsfred som ingen noe sted må forstyrre med utilbørlig larm."
    )
    assert _ground(inline, source, "§ 3")["grounded"] is True


# Exact wrap from the lovdata page: "§§ 3" is its own line, then "til", then "5".
_LOVDATA_SECTION_6 = (
    "§ 6.\n"
    "Straff\n"
    "Den som forsettlig eller uaktsomt overtrer \n"
    "§§ 3\n"
    " til \n"
    "5\n"
    " i denne lov, eller bestemmelser gitt i medhold av disse paragrafene, straffes med bøter.\n"
)


def test_lovdata_section_6_quote_survives_a_split_paragraph_sign():
    text = (FIXTURES / "lovdata_helligdagsfredloven_excerpt.txt").read_text(encoding="utf-8")
    source = _blocks(text)[0][1].rstrip() + "\n" + _LOVDATA_SECTION_6
    spans = grounding.bill_section_spans(source)
    keys = [span["key"] for span in spans]
    assert (("paragraphs", "3"),) not in keys
    section = next(span for span in spans if span["key"] == (("paragraph", "6"),))
    body = source[section["start"] : section["end"]]
    assert "Straff" in body
    assert "straffes med bøter" in body
    assert len(body) > 56
    located = _ground(_NO_SECTION_6, source, "§ 6")
    assert located["grounded"] is True, located["grounding_reason"]
    assert "§§ 3" in source[located["quote_start"] : located["quote_end"]]
    assert _ground(_NO_SECTION_6, source, "§ 3")["grounded"] is False
    # "§ 6." with a title line, and "§ 1 Formål" on one line, stay headings.
    titled = grounding.bill_section_spans("§ 1 Formål\nDenne loven skal verne gudstjenester og den offentlige ro.\n")
    assert titled[0]["key"] == (("paragraph", "1"),)
    # A bare "§ 3" between an unfinished line and a lowercase continuation is not a heading.
    wrapped = (
        "§ 6.\nStraff\n"
        "Den som forsettlig eller uaktsomt overtrer\n"
        "§ 3\n"
        " til 5 i denne lov, eller bestemmelser gitt i medhold av disse paragrafene, straffes med bøter.\n"
    )
    assert all(span["key"] != (("paragraph", "3"),) for span in grounding.bill_section_spans(wrapped))
    assert _ground(_NO_SECTION_6.replace("§§ ", "§ "), wrapped, "§ 6")["grounded"] is True


_ART_6 = (
    "W ustawie z dnia 16 grudnia 2016 r. o zasadach zarządzania mieniem państwowym "
    "w art. 3 w ust. 1 uchyla się pkt 32."
)


def test_art_6_amending_sentence_skips_the_journal_citation_only():
    text = (FIXTURES / "sejm_DU_2024_1823_excerpt.txt").read_text(encoding="utf-8")
    html = _blocks(text)[1][1]
    located = _ground(_ART_6, html, "Art. 6")
    assert located["grounded"] is True, located["grounding_reason"]
    span = html[located["quote_start"] : located["quote_end"]]
    assert "Dz. U." in span or "Dz.\xa0U." in span.replace(" ", "")
    assert "pkt 32" in span
    assert _ground(_ART_6, html, "Art. 5")["grounded"] is False
    changed = _ART_6.replace("pkt 32", "pkt 33")
    assert _ground(changed, html, "Art. 6")["grounded"] is False
    # A quote that states a different journal citation does not match by dropping both.
    other_citation = _ART_6.replace(
        "państwowym w art. 3",
        "państwowym (Dz. U. z 1999 r. poz. 1) w art. 3",
    )
    assert _ground(other_citation, html, "Art. 6")["grounded"] is False
    # An ordinary parenthesis is not a journal citation and is not skipped.
    assert grounding.accept_quote(
        "The Secretary shall publish subsidy recipients in this annual report.",
        "The Secretary (note) shall publish subsidy recipients in this annual report.",
    )["grounded"] is False


def test_journal_paren_cannot_hide_a_negation_or_an_amount():
    """Critic fixtures: a paren that only starts like a citation is not skipped."""
    negation_source = (
        "Art. 2. Minister (Dz. U. z 2024 r. poz. 1 — nie) przyznaje dotacji w wysokości "
        "pięćdziesięciu tysięcy złotych osobom uprawnionym na podstawie niniejszej ustawy.\n"
    )
    negation_quote = (
        "Minister przyznaje dotacji w wysokości pięćdziesięciu tysięcy złotych "
        "osobom uprawnionym na podstawie niniejszej ustawy."
    )
    hidden_negation = _ground(negation_quote, negation_source, "Art. 2")
    assert hidden_negation["grounded"] is False
    assert hidden_negation["grounding_reason"] == "quote_not_in_source"

    amount_source = (
        "Art. 3. Świadczenie wynosi sto (Dz. U. z 2024 r. poz. 1 tekst wstawia nie 500 a) "
        "złotych miesięcznie dla każdego uprawnionego beneficjenta programu wsparcia.\n"
    )
    amount_quote = (
        "Świadczenie wynosi sto złotych miesięcznie dla każdego uprawnionego "
        "beneficjenta programu wsparcia."
    )
    hidden_amount = _ground(amount_quote, amount_source, "Art. 3")
    assert hidden_amount["grounded"] is False
    assert hidden_amount["grounding_reason"] == "quote_not_in_source"

    prose_source = (
        "Art. 4. Obowiązek (Dz. U. z 2024 r. poz. 1 oraz art. 12 ust. 3 pkt 2 które zmieniają obowiązki) "
        "obejmuje każdego beneficjenta programu wsparcia społecznego w tym roku.\n"
    )
    prose_quote = "Obowiązek obejmuje każdego beneficjenta programu wsparcia społecznego w tym roku."
    assert _ground(prose_quote, prose_source, "Art. 4")["grounded"] is False

    two = (
        "Art. 5. Pierwszy fragment przepisu (Dz. U. z 2024 r. poz. 1) środkowy "
        "fragment przepisu (Dz. U. z 2024 r. poz. 2) jest załącznikiem do ustawy o wsparciu.\n"
    )
    bridged = "Pierwszy fragment przepisu środkowy fragment przepisu jest załącznikiem do ustawy o wsparciu."
    assert _ground(bridged, two, "Art. 5")["grounded"] is False
    # One real citation in the middle of the same sentence still grounds.
    one = two.replace(" (Dz. U. z 2024 r. poz. 2)", "")
    assert _ground(bridged, one, "Art. 5")["grounded"] is True


def test_short_official_title_can_ground_without_lowering_the_section_floor():
    official = "Lov om helligdager og helligdagsfred"
    assert len(grounding.visible_text(official)) < grounding.MIN_QUOTE_CHARS
    assert grounding.title_quote_meets_minimum(official)
    assert not grounding.quote_meets_minimum(official)
    bill = f"§ 1.\n{official}\nDenne loven skal verne gudstjenester og den offentlige ro på helligdager.\n"
    body = {
        "title": official,
        "title_quote": official,
        "plain_summary": "Holiday peace.",
        "plain_summary_quote": "Denne loven skal verne gudstjenester og den offentlige ro på helligdager.",
        "sections": [{
            "section_id": "§ 1",
            "heading": "Title",
            "summary": "Names the act.",
            "quote": official,
        }],
        "beneficiaries": {"benefits": [], "costs": [], "regulated": []},
        "red_flags": [],
        "comparable_laws": [],
        "open_questions": [],
        "metadata": {},
    }
    result = summarizer.analyze_bill(bill, provider=FakeProvider([json.dumps(body)]))
    assert result.title_grounded is True
    assert result.sections[0]["grounded"] is False
    assert result.sections[0]["grounding_reason"] == "quote_too_short"
    descriptive = dict(body)
    descriptive["title"] = "Norwegian Law on Public Holidays and Holiday Peace"
    missed = summarizer.analyze_bill(bill, provider=FakeProvider([json.dumps(descriptive)]))
    assert missed.title_grounded is False
    assert missed.title_grounding_reason == "title_not_in_quote"
