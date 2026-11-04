"""Shared fixtures: isolated state files and a fake model."""

from __future__ import annotations

import copy
from typing import Any, Callable

import pytest

from backend import audit, bills, config, llm, population, profile


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Point every state file at a temp directory."""
    monkeypatch.setattr(bills, "BILLS_DIR", tmp_path / "bills")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(population, "POPULATION_FILE", tmp_path / "population.json")
    monkeypatch.setattr(audit, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config, "POPULATION_SIZE", 20)
    monkeypatch.setattr(config, "BILL_COOLING_OFF_SECONDS", 0)
    monkeypatch.setattr(config, "ACCESS_TOKEN", "")
    return tmp_path


ANALYSIS = {
    "title": "Data Privacy Act",
    "plain_summary": "Limits government data collection.",
    "sections": [
        {"section_id": "Sec. 1", "heading": "Short title", "summary": "Names the act.",
         "significance": "low", "significance_reason": "Procedural."},
        {"section_id": "Sec. 2", "heading": "Warrant requirement", "summary": "Requires warrants.",
         "significance": "high", "significance_reason": "Core change."},
    ],
    "beneficiaries": {"benefits": ["residents"], "costs": ["agencies"], "regulated": ["agencies"]},
    "red_flags": [],
    "comparable_laws": [],
    "open_questions": [],
    "metadata": {"estimated_pages": 2, "complexity": "low", "primary_subject": "privacy",
                 "your_confidence": "high"},
}


class FakeLLM:
    """Stands in for llm.call_json. Responses are keyed by call name; a value
    may be a dict or a function of the call's kwargs. Every call is recorded."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, Any | Callable[..., Any]] = {
            "analysis": ANALYSIS,
            "vote": lambda **kw: {
                "section_votes": [
                    {"section_id": sid, "heading": "", "position": "yes", "basis": "values",
                     "reasoning": "Matches values.", "confidence": "high"}
                    for sid in kw["schema"]["properties"]["section_votes"]["items"]["properties"]["section_id"]["enum"]
                ],
                "recommendation": "Support.",
                "would_pass_sections": [],
                "key_tension": "",
            },
            "cross_check": {"votes": [{"section_id": "Sec. 2", "position": "no"}]},
            "drift": {"votes": []},
            "tag_sections": {"tags": [
                {"section_id": "Sec. 1", "economic": 0, "social": 0, "liberty": 0,
                 "environment": 0, "foreign": 0, "governance": 0},
                {"section_id": "Sec. 2", "economic": 0, "social": 0, "liberty": 0.9,
                 "environment": 0, "foreign": 0, "governance": 0},
            ]},
            "conflicts": {"conflicts": []},
        }

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        resp = self.responses[kwargs["name"]]
        return copy.deepcopy(resp(**kwargs) if callable(resp) else resp)

    def names(self) -> list[str]:
        return [c["name"] for c in self.calls]


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "call_json", fake)
    return fake
