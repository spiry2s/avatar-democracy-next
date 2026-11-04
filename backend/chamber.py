"""
The Proxy Chamber: aggregate a population of Avatars into a per-section result.

Cost is constant in the population size. We make ONE LLM call to "tag" each
bill section on the six political axes (which pole the section advances if
enacted). Every synthetic citizen then votes deterministically by aligning
their compass with those tags — a cheap dot product, no API call. The operator's
own Avatar still votes through the full engine (avatar.cast_vote) so their real
values/delegates count; its position is folded into the tally.

The output tally has the same shape bills.compute_result expects, so passage
(per-section, no Christmas-tree bills) is computed by the logic already tested.
"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import Anthropic

from backend.config import MODEL, CHAMBER_VOTE_TAU
from backend.questionnaire import AXES

AXIS_NAMES = list(AXES.keys())


def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set.")
    return Anthropic(api_key=api_key)


# ── deterministic compass vote (pure) ────────────────────────────────────────


def deterministic_vote(compass: dict[str, float], tag: dict[str, float], tau: float = CHAMBER_VOTE_TAU) -> str:
    """A citizen with this compass voting on a section with this axis tag.

    Alignment is the normalized dot product of compass and tag. A positive
    alignment means the section advances poles the citizen favors -> yes.
    """
    align = 0.0
    norm = 0.0
    for axis in AXIS_NAMES:
        t = float(tag.get(axis, 0.0))
        align += float(compass.get(axis, 0.0)) * t
        norm += abs(t)
    score = (align / norm) if norm > 0 else 0.0
    if score > tau:
        return "yes"
    if score < -tau:
        return "no"
    return "abstain"


def aggregate(
    analysis: dict[str, Any],
    operator_vote: dict[str, Any] | None,
    population: list[dict[str, Any]],
    section_tags: dict[str, dict[str, float]],
    tau: float = CHAMBER_VOTE_TAU,
) -> list[dict[str, Any]]:
    """Per-section vote counts across the operator + the synthetic population.

    Pure: takes pre-computed section_tags, returns tallies in compute_result shape.
    """
    op_positions: dict[str, str] = {}
    if operator_vote:
        for sv in operator_vote.get("section_votes", []):
            op_positions[str(sv.get("section_id"))] = sv.get("position", "abstain")

    tallies = []
    for section in analysis.get("sections", []):
        sid = str(section.get("section_id", "?"))
        tag = section_tags.get(sid, {})
        counts = {"yes": 0, "no": 0, "abstain": 0}
        by_archetype: dict[str, dict[str, int]] = {}  # aggregate blocs (privacy-safe; never per-citizen)

        # operator's real Avatar (full-engine vote)
        if operator_vote is not None:
            counts[op_positions.get(sid, "abstain")] += 1

        # synthetic population (deterministic), tallied by archetype bloc
        for citizen in population:
            v = deterministic_vote(citizen.get("compass", {}), tag, tau)
            counts[v] += 1
            bucket = by_archetype.setdefault(
                citizen.get("archetype", "?"), {"yes": 0, "no": 0, "abstain": 0}
            )
            bucket[v] += 1

        tallies.append({
            "section_id": sid,
            "heading": section.get("heading", ""),
            "yes": counts["yes"],
            "no": counts["no"],
            "abstain": counts["abstain"],
            "by_archetype": by_archetype,
        })
    return tallies


# ── section axis-tagging (one LLM call) ───────────────────────────────────────


def _axis_doc() -> str:
    return "\n".join(
        f"  - {axis}: -1 = advances \"{lab['neg']}\", +1 = advances \"{lab['pos']}\", 0 = irrelevant"
        for axis, lab in AXES.items()
    )


def _tag_prompt(analysis: dict[str, Any]) -> str:
    lines = [
        "Tag each section of this bill on six political axes. For each section and each axis,",
        "give a number in [-1, 1] for which pole the section ADVANCES if enacted (0 if the axis",
        "is irrelevant to that section). Judge the actual mechanism, not the bill's framing.",
        "",
        "Axes:",
        _axis_doc(),
        "",
        f"Bill: {analysis.get('title', '')}",
        "Sections:",
    ]
    for s in analysis.get("sections", []):
        lines.append(f"  [{s.get('section_id', '?')}] {s.get('heading', '')}: {s.get('summary', '')}")
    lines.append("")
    lines.append('Output ONLY JSON: {"<section_id>": {"economic": 0.0, "social": 0.0, "liberty": 0.0, '
                 '"environment": 0.0, "foreign": 0.0, "governance": 0.0}, ...}. No prose, no fences.')
    return "\n".join(lines)


def tag_sections(analysis: dict[str, Any], client: Anthropic | None = None) -> dict[str, dict[str, float]]:
    """One LLM call: map each section_id to a 6-axis tag vector."""
    if not analysis.get("sections"):
        return {}
    if client is None:
        client = get_client()

    message = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": _tag_prompt(analysis)}],
    )
    text = "".join(b.text for b in message.content if hasattr(b, "text")).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse axis tags as JSON: {e}\n\n{text[:400]}") from e

    # Normalize: ensure every section has all axes as floats in [-1, 1]
    tags: dict[str, dict[str, float]] = {}
    for sid, vec in raw.items():
        tags[str(sid)] = {
            axis: max(-1.0, min(1.0, float(vec.get(axis, 0.0)) if isinstance(vec, dict) else 0.0))
            for axis in AXIS_NAMES
        }
    return tags
