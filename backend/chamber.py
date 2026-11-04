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

Integrity rule: a section the tagger did not cover must NOT be quietly decided
by the operator alone (the whole population would abstain on it). Callers check
missing_tags() and refuse to record a result instead.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic

from backend import config, llm
from backend.llm import NUM, arr, obj, section_id_field
from backend.questionnaire import AXES
from backend.sections import WHOLE_BILL, lookup, section_ids

AXIS_NAMES = list(AXES.keys())


# ── deterministic compass vote (pure) ────────────────────────────────────────


def deterministic_vote(compass: dict[str, float], tag: dict[str, float], tau: float | None = None) -> str:
    """A citizen with this compass voting on a section with this axis tag.

    Alignment is the compass weighted by the tag, normalized by the tag's L1
    norm. A positive alignment means the section advances poles the citizen
    favors -> yes.
    """
    tau = config.CHAMBER_VOTE_TAU if tau is None else tau
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


def missing_tags(analysis: dict[str, Any], section_tags: dict[str, dict[str, float]]) -> list[str]:
    """Section ids that have no axis tag (exact or normalized match)."""
    return [sid for sid in section_ids(analysis) if lookup(section_tags, sid) is None]


def aggregate(
    analysis: dict[str, Any],
    operator_vote: dict[str, Any] | None,
    population: list[dict[str, Any]],
    section_tags: dict[str, dict[str, float]],
    tau: float | None = None,
) -> list[dict[str, Any]]:
    """Per-section vote counts across the operator + the synthetic population.

    Pure: takes pre-computed section_tags, returns tallies in compute_result shape.
    Section ids are matched exactly first, then by normalized form, so 'Sec. 101'
    in the analysis still finds a tag or vote keyed '101'.
    """
    op_positions: dict[str, str] = {}
    if operator_vote:
        for sv in operator_vote.get("section_votes", []):
            op_positions[str(sv.get("section_id"))] = sv.get("position", "abstain")

    headings = {str(s.get("section_id")): s.get("heading", "") for s in analysis.get("sections", [])}
    tallies = []
    for sid in section_ids(analysis):
        tag = lookup(section_tags, sid)
        counts = {"yes": 0, "no": 0, "abstain": 0}
        by_archetype: dict[str, dict[str, int]] = {}  # aggregate blocs (privacy-safe; never per-citizen)

        if operator_vote is not None:
            counts[lookup(op_positions, sid, "abstain")] += 1

        for citizen in population:
            v = deterministic_vote(citizen.get("compass", {}), tag or {}, tau)
            counts[v] += 1
            bucket = by_archetype.setdefault(
                citizen.get("archetype", "?"), {"yes": 0, "no": 0, "abstain": 0}
            )
            bucket[v] += 1

        tallies.append({
            "section_id": sid,
            "heading": headings.get(sid, ""),
            "yes": counts["yes"],
            "no": counts["no"],
            "abstain": counts["abstain"],
            "by_archetype": by_archetype,
            "tagged": tag is not None,
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
        "Return exactly one entry per section.",
        "",
        "Axes:",
        _axis_doc(),
        "",
        f"Bill: {analysis.get('title', '')}",
        "Sections:",
    ]
    sections = analysis.get("sections", [])
    if not sections:
        lines.append(f"  [{WHOLE_BILL}] (the whole bill): {analysis.get('plain_summary', '')}")
    for s in sections:
        lines.append(f"  [{s.get('section_id', '?')}] {s.get('heading', '')}: {s.get('summary', '')}")
    return "\n".join(lines)


def _tag_schema(analysis: dict[str, Any]) -> dict[str, Any]:
    return obj({"tags": arr(obj({
        "section_id": section_id_field(section_ids(analysis)),
        **{axis: NUM for axis in AXIS_NAMES},
    }))})


def tag_sections(analysis: dict[str, Any], client: Anthropic | None = None) -> dict[str, dict[str, float]]:
    """One LLM call: map each section_id to a 6-axis tag vector in [-1, 1]."""
    data = llm.call_json(
        name="tag_sections",
        model=config.MODEL,
        prompt=_tag_prompt(analysis),
        schema=_tag_schema(analysis),
        max_tokens=32000,
        client=client,
    )
    tags: dict[str, dict[str, float]] = {}
    for row in data.get("tags", []):
        tags[str(row["section_id"])] = {
            axis: max(-1.0, min(1.0, float(row.get(axis, 0.0)))) for axis in AXIS_NAMES
        }
    return tags
