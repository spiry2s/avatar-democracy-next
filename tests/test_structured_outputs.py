"""Issue #14: model replies are constrained by JSON Schemas (structured outputs)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend import avatar, chamber, conflict, llm, schemas, summarizer
from backend.profile import CitizenProfile


# ── every schema follows the API's rules ────────────────────────────────────


def _walk(node: Any, path: str = "$"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")


ALL_SCHEMAS = {
    "analysis": schemas.analysis_schema(),
    "vote": schemas.vote_schema(["Sec. 1", "Sec. 2"]),
    "positions": schemas.positions_schema(["Sec. 1", "Sec. 2"]),
    "tags": schemas.tags_schema(["Sec. 1"]),
    "conflicts": schemas.conflicts_schema(["bill-1"]),
}


@pytest.mark.parametrize("name", sorted(ALL_SCHEMAS))
def test_schema_follows_structured_output_rules(name):
    for path, node in _walk(ALL_SCHEMAS[name]):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, f"{name} {path}"
            assert sorted(node["required"]) == sorted(node["properties"]), f"{name} {path}"
        for unsupported in ("minimum", "maximum", "multipleOf", "minLength", "maxLength"):
            assert unsupported not in node, f"{name} {path} uses {unsupported}"


def test_keyed_schemas_require_every_section_and_none_when_empty():
    positions = schemas.positions_schema(["a", "b", "a"])
    assert positions["required"] == ["a", "b"]
    assert positions["properties"]["a"]["enum"] == ["yes", "no", "abstain"]
    assert schemas.positions_schema([]) is None
    assert schemas.tags_schema([]) is None


# ── the Anthropic provider ───────────────────────────────────────────────────


class _Msg:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(type="text", text=text)]
        self.stop_reason = "end_turn"
        self.usage = SimpleNamespace(input_tokens=1, output_tokens=1)


class _Rejected(Exception):
    status_code = 400

    def __str__(self):
        return "output_config.format: structured outputs are not supported for this model"


@pytest.fixture
def anthropic_calls(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("STRUCTURED_OUTPUTS", raising=False)
    monkeypatch.setattr(llm, "_SCHEMA_UNSUPPORTED", set())
    sent: list[dict[str, Any]] = []
    replies: list[Any] = []

    def fake_final(client, kwargs):
        sent.append(dict(kwargs))
        reply = replies.pop(0) if replies else _Msg('{"a": "yes"}')
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "_anthropic_final_message", fake_final)
    monkeypatch.setattr(llm.AnthropicProvider, "_anthropic", lambda self: object())
    return sent, replies


SCHEMA = schemas.positions_schema(["a"])


def test_anthropic_sends_the_schema_as_output_config(anthropic_calls):
    sent, _ = anthropic_calls
    provider = llm.AnthropicProvider(model="claude-sonnet-4-6")

    assert provider.complete_json(user="u", schema=SCHEMA) == {"a": "yes"}
    assert sent[0]["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_no_schema_means_no_output_config(anthropic_calls):
    sent, _ = anthropic_calls
    llm.AnthropicProvider(model="claude-sonnet-4-6").complete_json(user="u")
    assert "output_config" not in sent[0]


def test_model_that_rejects_schemas_falls_back_and_is_remembered(anthropic_calls):
    sent, replies = anthropic_calls
    replies.extend([_Rejected(), _Msg('{"a": "no"}'), _Msg('{"a": "yes"}')])
    provider = llm.AnthropicProvider(model="claude-old-model")

    assert provider.complete_json(user="u", schema=SCHEMA) == {"a": "no"}
    assert "output_config" in sent[0] and "output_config" not in sent[1]
    assert "claude-old-model" in llm._SCHEMA_UNSUPPORTED

    provider.complete_json(user="u", schema=SCHEMA)
    assert "output_config" not in sent[2]  # not retried with a schema


def test_other_errors_are_not_swallowed(anthropic_calls):
    _, replies = anthropic_calls

    class _Other(Exception):
        status_code = 400

        def __str__(self):
            return "messages: field required"

    replies.append(_Other())
    with pytest.raises(_Other):
        llm.AnthropicProvider(model="claude-sonnet-4-6").complete_json(user="u", schema=SCHEMA)


def test_switch_turns_schemas_off(anthropic_calls, monkeypatch):
    sent, _ = anthropic_calls
    monkeypatch.setenv("STRUCTURED_OUTPUTS", "0")
    llm.AnthropicProvider(model="claude-sonnet-4-6").complete_json(user="u", schema=SCHEMA)
    assert "output_config" not in sent[0]


# ── each call site passes the matching schema ────────────────────────────────


class Recorder(llm.LLMProvider):
    provider_id = "fake"
    model = "fake"

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.schemas: list[dict[str, Any] | None] = []

    def accepts_schema(self) -> bool:
        return True

    def complete(self, *, user, system=None, max_tokens=None, data_blocks=None, schema=None) -> str:
        self.schemas.append(schema)
        return self.replies.pop(0)


ANALYSIS = {
    "title": "Grain tax",
    "sections": [
        {"section_id": "Sec. 1", "heading": "Tax", "summary": "Taxes grain.", "grounded": True},
        {"section_id": "Sec. 2", "heading": "Rider", "summary": "?", "grounded": False},
        {"section_id": "Sec. 3", "heading": "Rate", "summary": "Sets the rate.", "grounded": True},
    ],
    "red_flags": [],
}


def test_vote_and_checker_list_only_the_sections_in_the_prompt(monkeypatch):
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", True)
    vote = json.dumps({
        "section_votes": [
            {"section_id": sid, "heading": "", "position": "yes", "basis": "values",
             "reasoning": "r", "confidence": "high"}
            for sid in ("Sec. 1", "Sec. 3")
        ],
        "recommendation": "", "would_pass_sections": [], "key_tension": "",
    })
    primary, checker = Recorder(vote), Recorder(json.dumps({"Sec. 1": "yes", "Sec. 3": "no"}))

    avatar.cast_vote(ANALYSIS, CitizenProfile(values="v"), provider=primary, checker=checker)

    vote_ids = primary.schemas[0]["properties"]["section_votes"]["items"]["properties"]["section_id"]["enum"]
    assert vote_ids == ["Sec. 1", "Sec. 3"]  # the withheld Sec. 2 is not offered
    assert checker.schemas[0]["required"] == ["Sec. 1", "Sec. 3"]


def test_axis_tags_require_every_voteable_section():
    tagger = Recorder(json.dumps({"Sec. 1": {a: 0.1 for a in chamber.AXIS_NAMES},
                                  "Sec. 3": {a: 0.2 for a in chamber.AXIS_NAMES}}))
    tags = chamber.tag_sections(ANALYSIS, provider=tagger)
    assert tagger.schemas[0]["required"] == ["Sec. 1", "Sec. 3"]
    assert sorted(tags) == ["Sec. 1", "Sec. 3"]


def test_conflict_ids_are_limited_to_passed_bills():
    checker = Recorder(json.dumps({"conflicts": [{"bill_id": "bill-a", "conflict": "Repeals it."}]}))
    found = conflict.detect_conflicts(
        {"title": "New", "plain_summary": "s", "sections": []},
        [{"id": "bill-a", "title": "Old", "summary": "s"}],
        provider=checker,
    )
    item = checker.schemas[0]["properties"]["conflicts"]["items"]["properties"]["bill_id"]
    assert item["enum"] == ["bill-a"]
    assert found[0]["bill_id"] == "bill-a"


def test_analysis_request_carries_the_analysis_schema(monkeypatch):
    seen: list[Any] = []

    class Analyzer(Recorder):
        def complete(self, **kwargs):
            seen.append(kwargs.get("schema"))
            raise ValueError("stop here")

    with pytest.raises(ValueError):
        summarizer.analyze_bill("Section 1. " + "Bill text. " * 20, provider=Analyzer())
    assert seen and seen[0] == schemas.analysis_schema()
