"""Chamber integrity: section ids in other formats, and sections the tagger missed.

Issue #1: tags and the operator's vote come from separate model calls that may
format a section id differently. An exact-only lookup made the whole population
abstain on that section, so one operator vote decided it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from backend import bills as bills_mod, chamber, population, profile

POP = [{"archetype": "a", "compass": {"liberty": 0.9}}] * 200


def _tally(analysis: dict[str, Any], operator: dict[str, Any] | None, tags: dict[str, Any]) -> dict[str, Any]:
    (row,) = chamber.aggregate(analysis, operator, POP, tags, tau=0.12)
    return row


def test_tag_keyed_without_sec_prefix_still_reaches_the_population():
    analysis = {"sections": [{"section_id": "Sec. 101", "heading": "H"}]}
    row = _tally(analysis, {"section_votes": [{"section_id": "Sec. 101", "position": "no"}]}, {"101": {"liberty": 0.9}})
    assert row["tagged"] is True
    assert (row["yes"], row["no"], row["abstain"]) == (200, 1, 0)


def test_operator_vote_in_another_id_format_is_counted():
    analysis = {"sections": [{"section_id": "Section 7", "heading": "H"}]}
    row = _tally(analysis, {"section_votes": [{"section_id": "sec_7", "position": "no"}]}, {"Section 7": {"liberty": 0.9}})
    assert row["no"] == 1


def test_operator_position_case_is_normalized():
    analysis = {"sections": [{"section_id": "S1", "heading": "H"}]}
    row = _tally(analysis, {"section_votes": [{"section_id": "S1", "position": " YES "}]}, {"S1": {"liberty": 0.9}})
    assert row["yes"] == 201


def test_review_flag_found_across_id_formats():
    analysis = {"sections": [{"section_id": "Sec. 2", "heading": "H"}]}
    operator = {"section_votes": [{"section_id": "2", "position": "yes", "needs_human_review": True}]}
    row = _tally(analysis, operator, {"Sec. 2": {"liberty": 0.9}})
    assert row["needs_human_review"] == 1 and row["yes"] == 200


def test_untagged_section_is_marked():
    analysis = {"sections": [{"section_id": "S1", "heading": "H"}]}
    row = _tally(analysis, None, {})
    assert row["tagged"] is False


def test_missing_tags_lists_only_voteable_sections():
    analysis = {"sections": [
        {"section_id": "Sec. 1"},
        {"section_id": "Sec. 2"},
        {"section_id": "Sec. 3", "grounded": False},  # withheld, never sent for tagging
    ]}
    assert chamber.missing_tags(analysis, {"1": {}}) == ["Sec. 2"]


# ── endpoint: an untagged section records no result ──────────────────────────


@pytest.fixture
def voting_bill(tmp_path, monkeypatch):
    monkeypatch.delenv("AVATAR_INVITE_MODE", raising=False)
    monkeypatch.setattr(bills_mod, "BILLS_DIR", tmp_path / "bills")
    monkeypatch.setattr(profile, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(population, "POPULATION_FILE", tmp_path / "population.json")
    monkeypatch.setattr(main.audit, "AUDIT_FILE", tmp_path / "audit.jsonl")
    profile.save_profile(profile.CitizenProfile(values="privacy first"))
    population.save_population(POP)

    bill = bills_mod.propose("Privacy", "x" * 200)
    bill.state = bills_mod.BillState.VOTING
    bill.analysis = {
        "title": "Privacy",
        "sections": [
            {"section_id": "Sec. 1", "heading": "Short title", "grounded": True},
            {"section_id": "Sec. 2", "heading": "Warrants", "grounded": True},
        ],
    }
    bills_mod.save_bill(bill)

    class Vote:
        def to_dict(self):
            return {"section_votes": [
                {"section_id": "Sec. 1", "position": "yes"},
                {"section_id": "Sec. 2", "position": "yes"},
            ]}

    monkeypatch.setattr(main.avatar, "cast_vote", lambda analysis, prof: Vote())
    return bill.id


def test_vote_with_untagged_section_records_nothing(voting_bill, monkeypatch):
    monkeypatch.setattr(main.chamber, "tag_sections", lambda analysis: {"1": {"liberty": 0.9}})
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")

    response = client.post(f"/api/bills/{voting_bill}/vote")
    assert response.status_code == 502
    assert "Sec. 2" in response.json()["detail"]

    stored = bills_mod.load_bill(voting_bill)
    assert stored.state == bills_mod.BillState.VOTING
    assert stored.result is None


def test_vote_with_reformatted_tag_ids_counts_the_population(voting_bill, monkeypatch):
    monkeypatch.setattr(
        main.chamber,
        "tag_sections",
        lambda analysis: {"1": {"liberty": 0.9}, "2": {"liberty": 0.9}},
    )
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")

    response = client.post(f"/api/bills/{voting_bill}/vote")
    assert response.status_code == 200, response.text
    for section in response.json()["bill"]["result"]["per_section"]:
        assert section["yes"] == 201, section


# ── issue #2: unverified sections must not pass without a vote ───────────────


def test_unverified_section_blocks_the_bill():
    analysis = {"sections": [
        {"section_id": "Sec. 1", "heading": "Popular"},
        {"section_id": "Sec. 2", "heading": "Unverified rider", "grounded": False},
    ]}
    operator = {"section_votes": [
        {"section_id": "Sec. 1", "position": "yes"},
        {"section_id": "Sec. 2", "position": "abstain", "grounding_block": True},
    ]}
    tallies = chamber.aggregate(analysis, operator, POP, {"Sec. 1": {"liberty": 0.9}}, tau=0.12)
    result = bills_mod.compute_result(tallies, 0.5)

    assert result["passed"] is False
    assert result["sections_withheld"] == ["Sec. 2"]
    assert "Sec. 2" in result["blocked_reason"]
    rider = next(s for s in result["per_section"] if s["section_id"] == "Sec. 2")
    assert rider["withheld"] is True and rider["contested"] is False


def test_genuine_no_contest_section_still_does_not_block():
    tallies = [
        {"section_id": "a", "heading": "", "yes": 5, "no": 0, "abstain": 0},
        {"section_id": "title", "heading": "", "yes": 0, "no": 0, "abstain": 5},
    ]
    result = bills_mod.compute_result(tallies, 0.5)
    assert result["passed"] is True
    assert result["sections_withheld"] == []
    assert "blocked_reason" not in result


def test_withheld_section_with_stray_votes_still_blocks():
    tallies = [
        {"section_id": "a", "heading": "", "yes": 5, "no": 0, "abstain": 0},
        {"section_id": "b", "heading": "", "yes": 3, "no": 0, "abstain": 0, "withheld": True},
    ]
    result = bills_mod.compute_result(tallies, 0.5)
    assert result["passed"] is False
    assert result["sections_contested"] == 1
