"""
Unit tests for the Proxy Chamber's pure logic (backend/chamber.py and
backend/population.py). No API calls — the LLM axis-tagging is not exercised here;
we pass tags in directly.
"""

from backend import chamber, population


def test_deterministic_vote_aligned_yes():
    # A strong civil-libertarian (liberty +) on a section that advances liberty (+).
    compass = {"liberty": 0.8}
    tag = {"liberty": 0.9}
    assert chamber.deterministic_vote(compass, tag, tau=0.12) == "yes"


def test_deterministic_vote_opposed_no():
    # Same citizen on a section that advances security/order (liberty -).
    compass = {"liberty": 0.8}
    tag = {"liberty": -0.9}
    assert chamber.deterministic_vote(compass, tag, tau=0.12) == "no"


def test_deterministic_vote_irrelevant_abstains():
    # Section with no axis lean -> abstain regardless of compass.
    compass = {"liberty": 0.9, "economic": -0.8}
    tag = {}  # all zero
    assert chamber.deterministic_vote(compass, tag, tau=0.12) == "abstain"


def test_deterministic_vote_dominant_axis_decides():
    # Economic pull outweighs a weak opposing liberty pull.
    compass = {"economic": 0.9, "liberty": 0.2}
    tag = {"economic": 0.9, "liberty": -0.1}
    assert chamber.deterministic_vote(compass, tag, tau=0.12) == "yes"


def test_aggregate_counts_operator_plus_population():
    analysis = {"sections": [
        {"section_id": "s1", "heading": "H1"},
        {"section_id": "s2", "heading": "H2"},
    ]}
    operator_vote = {"section_votes": [
        {"section_id": "s1", "position": "yes"},
        {"section_id": "s2", "position": "no"},
    ]}
    population_sample = [
        {"archetype": "libertarian", "compass": {"liberty": 0.9}},   # pro-liberty
        {"archetype": "conservative", "compass": {"liberty": -0.9}}, # pro-security
    ]
    tags = {
        "s1": {"liberty": 0.9},   # advances civil liberties
        "s2": {"liberty": 0.9},
    }
    tallies = chamber.aggregate(analysis, operator_vote, population_sample, tags, tau=0.12)

    s1 = next(t for t in tallies if t["section_id"] == "s1")
    s2 = next(t for t in tallies if t["section_id"] == "s2")
    # s1: operator yes + pro-liberty yes + pro-security no
    assert (s1["yes"], s1["no"], s1["abstain"]) == (2, 1, 0)
    # s2: operator no + pro-liberty yes + pro-security no
    assert (s2["yes"], s2["no"], s2["abstain"]) == (1, 2, 0)
    # per-archetype bloc breakdown (aggregate)
    assert s1["by_archetype"]["libertarian"]["yes"] == 1
    assert s1["by_archetype"]["conservative"]["no"] == 1


def test_needs_human_review_is_not_an_automatic_yes_or_no():
    analysis = {"sections": [
        {"section_id": "s1", "heading": "H1"},
        {"section_id": "s2", "heading": "H2"},
    ]}
    operator_vote = {"section_votes": [
        {"section_id": "s1", "position": "yes", "needs_human_review": True},
        {"section_id": "s2", "position": "no", "needs_human_review": False},
    ]}
    tallies = chamber.aggregate(analysis, operator_vote, [], {}, tau=0.12)
    s1 = next(t for t in tallies if t["section_id"] == "s1")
    s2 = next(t for t in tallies if t["section_id"] == "s2")
    assert (s1["yes"], s1["no"], s1["abstain"], s1["needs_human_review"]) == (0, 0, 0, 1)
    assert (s2["yes"], s2["no"], s2["abstain"], s2["needs_human_review"]) == (0, 1, 0, 0)


def test_aggregate_without_operator():
    analysis = {"sections": [{"section_id": "s1", "heading": "H1"}]}
    pop = [{"compass": {"economic": 0.8}}, {"compass": {"economic": -0.8}}]
    tags = {"s1": {"economic": 0.9}}
    tallies = chamber.aggregate(analysis, None, pop, tags, tau=0.12)
    assert (tallies[0]["yes"], tallies[0]["no"]) == (1, 1)


def test_distribution_aggregates():
    citizens = [
        {"archetype": "libertarian", "compass": {"economic": 1.0, "social": 0.0}},
        {"archetype": "progressive", "compass": {"economic": -1.0, "social": -0.5}},
    ]
    d = population.distribution(citizens)
    assert d["size"] == 2
    assert d["archetypes"] == {"libertarian": 1, "progressive": 1}
    assert d["axis_means"]["economic"] == 0.0          # (1 + -1) / 2
    assert round(d["axis_means"]["social"], 3) == -0.25  # (0 + -0.5) / 2
    assert set(population.AXIS_NAMES).issubset(d["axis_means"].keys())
    assert len(d["citizens"]) == 2


def test_population_generation_is_deterministic_with_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(population, "POPULATION_FILE", tmp_path / "pop.json")
    a = population.generate_population(size=50, seed=42)
    b = population.generate_population(size=50, seed=42)
    assert len(a) == 50
    assert [c["compass"] for c in a] == [c["compass"] for c in b]
    # archetypes are drawn from the known set
    assert all(c["archetype"] in population.ARCHETYPES for c in a)
