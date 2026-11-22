"""
JSON Schemas for every model reply (issue #14).

Passed as ``schema=`` to ``LLMProvider.complete_json``. The Anthropic provider
sends them as structured outputs (``output_config.format``), so the API
enforces the shape: the reply is always one JSON object of that shape, never
prose or a fenced block. Providers without structured outputs ignore the
schema and the reply is parsed from text as before.

Where a reply is keyed by section id, the schema lists the bill's actual ids
as required properties. The model then cannot skip a section or spell its id
differently, which closes the id-mismatch class of bug at the source.

Rules the API sets: every object has ``additionalProperties: false``, and
numeric limits (``minimum``/``maximum``) are not supported, so ranges are
still enforced in code. The existing validators run on every reply anyway.
"""

from __future__ import annotations

from typing import Any, Iterable

from backend.grounding import FLAG_TYPES, SEVERITY, SIGNIFICANCE
from backend.questionnaire import AXES

POSITION = {"type": "string", "enum": ["yes", "no", "abstain"]}
LEVEL = {"type": "string", "enum": sorted(SIGNIFICANCE)}
STR = {"type": "string"}
NUM = {"type": "number"}
OPTIONAL_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def obj(properties: dict[str, Any]) -> dict[str, Any]:
    """A closed object whose properties are all required."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _ids(section_ids: Iterable[Any]) -> list[str]:
    seen = dict.fromkeys(str(s) for s in section_ids if str(s).strip())
    return list(seen)


def _quoted(properties: dict[str, Any]) -> dict[str, Any]:
    return obj({**properties, "quote": STR, "quote_start": OPTIONAL_INT, "quote_end": OPTIONAL_INT})


def analysis_schema() -> dict[str, Any]:
    """The neutral bill analysis (summarizer.ANALYSIS_PROMPT)."""
    claim = _quoted({"claim": STR})
    return obj({
        "title": STR,
        "title_quote": STR,
        "plain_summary": STR,
        "plain_summary_quote": STR,
        "sections": arr(_quoted({
            "section_id": STR,
            "heading": STR,
            "summary": STR,
            "significance": LEVEL,
            "significance_reason": STR,
        })),
        "beneficiaries": obj({"benefits": arr(claim), "costs": arr(claim), "regulated": arr(claim)}),
        "red_flags": arr(_quoted({
            "type": {"type": "string", "enum": sorted(FLAG_TYPES)},
            "section": STR,
            "description": STR,
            "severity": {"type": "string", "enum": sorted(SEVERITY)},
        })),
        "comparable_laws": arr(STR),
        "open_questions": arr(STR),
        "metadata": obj({
            "estimated_pages": {"type": "integer"},
            "complexity": LEVEL,
            "primary_subject": STR,
            "your_confidence": LEVEL,
        }),
    })


def vote_schema(section_ids: Iterable[Any]) -> dict[str, Any]:
    """The Avatar's vote. section_id is limited to the ids in the prompt."""
    ids = _ids(section_ids)
    sid = {"type": "string", "enum": ids} if ids else STR
    return obj({
        "section_votes": arr(obj({
            "section_id": sid,
            "heading": STR,
            "position": POSITION,
            "basis": STR,
            "reasoning": STR,
            "confidence": LEVEL,
        })),
        "recommendation": STR,
        "would_pass_sections": arr(sid),
        "key_tension": STR,
    })


def positions_schema(section_ids: Iterable[Any]) -> dict[str, Any] | None:
    """{"<section_id>": "yes" | "no" | "abstain"} with every id required.

    Used by the checker pass and the delegate-drift check. None when there
    are no ids, so the caller sends no schema.
    """
    ids = _ids(section_ids)
    return obj({sid: POSITION for sid in ids}) if ids else None


def tags_schema(section_ids: Iterable[Any]) -> dict[str, Any] | None:
    """{"<section_id>": {axis: number, ...}} with every id and axis required."""
    ids = _ids(section_ids)
    if not ids:
        return None
    vector = obj({axis: NUM for axis in AXES})
    return obj({sid: vector for sid in ids})


def conflicts_schema(bill_ids: Iterable[Any]) -> dict[str, Any]:
    """{"conflicts": [{"bill_id", "conflict"}]}. bill_id is limited to real passed bills."""
    ids = _ids(bill_ids)
    bill_id = {"type": "string", "enum": ids} if ids else STR
    return obj({"conflicts": arr(obj({"bill_id": bill_id, "conflict": STR}))})
