"""Issue #9: the Avatar's vote is normalized, and failed checks are reported.

- A position other than yes, no, or abstain counts as abstain, with a note.
- would_pass_sections is derived from the final positions.
- A checker or drift check that fails is listed in checks_unavailable
  instead of being swallowed, so the vote does not look checked.
"""

from __future__ import annotations

import json
from typing import Any

from backend import avatar, llm
from backend.profile import CitizenProfile

ANALYSIS = {
    "title": "Grain tax",
    "sections": [
        {"section_id": "s1", "heading": "Tax", "summary": "Taxes grain."},
        {"section_id": "s2", "heading": "Rate", "summary": "Sets the rate."},
        {"section_id": "s3", "heading": "Report", "summary": "Requires a report."},
    ],
    "red_flags": [],
}


class Scripted(llm.LLMProvider):
    provider_id = "fake"
    model = "fake"

    def __init__(self, *replies: Any):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, *, user, system=None, max_tokens=None, data_blocks=None) -> str:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _vote_json(*positions: str, would=("s9",), confidence="high") -> str:
    return json.dumps({
        "section_votes": [
            {"section_id": f"s{i}", "heading": "", "position": pos, "basis": "values",
             "reasoning": "Because.", "confidence": confidence}
            for i, pos in enumerate(positions, start=1)
        ],
        "recommendation": "ok",
        "would_pass_sections": list(would),
        "key_tension": "",
    })


def _vote(primary: Scripted, checker: Scripted | None = None, profile: CitizenProfile | None = None, monkeypatch=None):
    return avatar.cast_vote(ANALYSIS, profile or CitizenProfile(values="v"), provider=primary, checker=checker)


def test_positions_are_normalized_and_unknown_ones_abstain(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)

    result = _vote(Scripted(_vote_json(" YES ", "No", "support")))

    positions = [v.position for v in result.section_votes]
    assert positions == ["yes", "no", "abstain"]
    odd = result.section_votes[2]
    assert odd.confidence == "low"
    assert "'support'" in odd.reasoning and "counts as abstain" in odd.reasoning


def test_unknown_confidence_becomes_low(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)

    result = _vote(Scripted(_vote_json("yes", "yes", "yes", confidence="very sure")))

    assert {v.confidence for v in result.section_votes} == {"low"}


def test_would_pass_is_derived_from_the_final_positions(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)

    result = _vote(Scripted(_vote_json("yes", "no", "yes", would=["s2", "s9"])))

    assert result.would_pass_sections == ["s1", "s3"]


def test_failed_checker_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", True)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)

    result = _vote(Scripted(_vote_json("yes", "no", "yes")), checker=Scripted(TimeoutError("slow")))

    assert result.checks_unavailable == ["cross_check: TimeoutError"]
    assert result.to_dict()["checks_unavailable"] == ["cross_check: TimeoutError"]
    assert [v.position for v in result.section_votes] == ["yes", "no", "yes"]  # the vote still stands


def test_failed_drift_check_is_reported(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", False)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", True)
    profile = CitizenProfile(values="v")
    profile.add_delegate("tax", "Ada Tax")
    profile.delegates[0].active_at = 0.0
    profile.add_source("tax", "2024 statement", "Opposes new grain taxes.")
    primary = Scripted(json.dumps({
        "section_votes": [{"section_id": "s1", "heading": "", "position": "no",
                           "basis": "delegate:Ada Tax", "reasoning": "r", "confidence": "high"}],
        "recommendation": "", "would_pass_sections": [], "key_tension": "",
    }))

    result = _vote(primary, checker=Scripted(ValueError("bad json")), profile=profile)

    assert result.checks_unavailable == ["delegate_drift: ValueError"]


def test_checks_that_ran_leave_the_list_empty(monkeypatch):
    monkeypatch.setattr(avatar, "MULTI_MODEL_CHECK", True)
    monkeypatch.setattr(avatar, "DELEGATE_DRIFT_CHECK", False)

    result = _vote(
        Scripted(_vote_json("yes", "no", "yes")),
        checker=Scripted(json.dumps({"s1": "yes", "s2": "no", "s3": "yes"})),
    )

    assert result.checks_unavailable == []
