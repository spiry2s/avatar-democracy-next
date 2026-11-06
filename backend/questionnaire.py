"""
The seeding questionnaire for an Avatar's political-compass profile.

This is step 1 of the hybrid configuration model: a short set of Likert-scale
statements that produce a multi-axis "compass". The citizen then refines this
with a free-text values statement and per-issue delegates (see profile.py).

The compass is deliberately multi-axis rather than a single left/right line,
because the Avatar votes per-issue and needs per-domain signal. The numbers are
a fallback the Avatar uses only when no delegate and no values statement covers
a given section.
"""

from __future__ import annotations

from typing import Any

# Each axis runs from -1.0 to +1.0. The labels describe each pole so the Avatar
# (and the UI) can render the score in plain language rather than raw numbers.
AXES: dict[str, dict[str, str]] = {
    "economic": {"neg": "state-led / redistributive", "pos": "free-market"},
    "social": {"neg": "progressive", "pos": "traditional"},
    "liberty": {"neg": "security / order", "pos": "civil liberties"},
    "environment": {"neg": "climate-priority", "pos": "growth-priority"},
    "foreign": {"neg": "non-interventionist", "pos": "interventionist"},
    "governance": {"neg": "centralized / federal", "pos": "localized / states"},
}

# Likert scale presented to the citizen. Value is the raw contribution.
SCALE = [
    {"value": -2, "label": "Strongly disagree"},
    {"value": -1, "label": "Disagree"},
    {"value": 0, "label": "Neutral"},
    {"value": 1, "label": "Agree"},
    {"value": 2, "label": "Strongly agree"},
]

# id -> (statement, axis, direction). direction=+1 means "agree" pushes the axis
# toward its positive pole; -1 means "agree" pushes toward the negative pole.
QUESTIONS: list[dict[str, Any]] = [
    {"id": "econ_1", "axis": "economic", "direction": 1,
     "statement": "The government should reduce taxes and public spending, even if that means fewer public services."},
    {"id": "econ_2", "axis": "economic", "direction": -1,
     "statement": "Essential services like healthcare and utilities should be publicly run, not left to the market."},
    {"id": "soc_1", "axis": "social", "direction": 1,
     "statement": "Traditional family and community values should guide social policy."},
    {"id": "soc_2", "axis": "social", "direction": -1,
     "statement": "Society should actively change to expand individual freedom in personal and family life."},
    {"id": "lib_1", "axis": "liberty", "direction": 1,
     "statement": "Individual privacy and civil liberties should take priority over state security powers."},
    {"id": "lib_2", "axis": "liberty", "direction": -1,
     "statement": "The government should have strong surveillance and policing powers to keep people safe."},
    {"id": "env_1", "axis": "environment", "direction": -1,
     "statement": "Protecting the climate should take priority even at significant economic cost."},
    {"id": "env_2", "axis": "environment", "direction": 1,
     "statement": "Economic growth and jobs should take priority over environmental regulation."},
    {"id": "for_1", "axis": "foreign", "direction": 1,
     "statement": "Our country should actively intervene abroad to defend its interests and allies."},
    {"id": "for_2", "axis": "foreign", "direction": -1,
     "statement": "Our country should avoid foreign entanglements and focus on problems at home."},
    {"id": "gov_1", "axis": "governance", "direction": 1,
     "statement": "Decisions should be made as locally as possible, close to the people affected."},
    {"id": "gov_2", "axis": "governance", "direction": -1,
     "statement": "A strong central government is needed to set consistent national standards."},
]


SCALE_VALUES = frozenset({-2, -1, 0, 1, 2})


def clean_answers(raw: dict[str, Any]) -> dict[str, int]:
    """Keep known question ids and Likert values from -2 to 2.

    Unknown ids and values outside that scale are rejected. Booleans are
    rejected so a JSON true cannot become an agreement.
    """
    if not isinstance(raw, dict):
        raise ValueError("Answers must be a map of question id to a scale value.")
    known = {q["id"] for q in QUESTIONS}
    cleaned: dict[str, int] = {}
    for key, value in raw.items():
        if key not in known or isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Answers must use the questionnaire ids and values from -2 to 2.")
        if value not in SCALE_VALUES:
            raise ValueError("Answers must use the questionnaire ids and values from -2 to 2.")
        cleaned[key] = value
    return cleaned


def public_questionnaire() -> dict[str, Any]:
    """The questionnaire as sent to the frontend (no scoring internals beyond what's needed)."""
    return {
        "scale": SCALE,
        "questions": [{"id": q["id"], "statement": q["statement"]} for q in QUESTIONS],
        "axes": AXES,
    }


def score(answers: dict[str, int]) -> dict[str, float]:
    """
    Convert raw Likert answers into a normalized -1..1 score per axis.

    Args:
        answers: mapping of question id -> Likert value (-2..2).

    Returns:
        mapping of axis name -> score in [-1, 1]. Unanswered axes default to 0.
    """
    totals: dict[str, float] = {axis: 0.0 for axis in AXES}
    counts: dict[str, int] = {axis: 0 for axis in AXES}

    by_id = {q["id"]: q for q in QUESTIONS}
    for qid, raw in answers.items():
        q = by_id.get(qid)
        if q is None:
            continue
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        val = max(-2, min(2, val))
        totals[q["axis"]] += val * q["direction"]
        counts[q["axis"]] += 1

    compass: dict[str, float] = {}
    for axis in AXES:
        if counts[axis] == 0:
            compass[axis] = 0.0
        else:
            # max magnitude per question is 2, so divide by 2 * count
            compass[axis] = round(totals[axis] / (2 * counts[axis]), 3)
    return compass


def describe_compass(compass: dict[str, float]) -> str:
    """Render a compass dict as human-readable lines for the voting prompt."""
    lines = []
    for axis, score_val in compass.items():
        labels = AXES.get(axis, {"neg": "low", "pos": "high"})
        if abs(score_val) < 0.15:
            lean = "balanced / no strong lean"
        else:
            pole = labels["pos"] if score_val > 0 else labels["neg"]
            strength = "strongly" if abs(score_val) >= 0.6 else "leans"
            lean = f"{strength} {pole}"
        lines.append(f"  - {axis}: {score_val:+.2f} ({lean})")
    return "\n".join(lines)
