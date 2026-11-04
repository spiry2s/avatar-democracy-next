"""
Cross-bill conflict detection.

When a bill freezes, check whether it conflicts with already-passed bills —
contradicts, repeals, overrides, duplicates, or is legally incompatible with them.
One cheap LLM call; best-effort (a failure never blocks the lifecycle).
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic

from backend import config, llm
from backend.llm import STR, arr, enum, obj


def detect_conflicts(
    analysis: dict[str, Any],
    passed_bills: list[dict[str, Any]],
    client: Anthropic | None = None,
) -> list[dict[str, Any]]:
    """Compare a new bill's analysis against previously passed bills.

    Args:
        analysis: the new bill's analysis dict (title, plain_summary, sections).
        passed_bills: [{"id", "title", "summary"}] of already-passed bills.

    Returns:
        [{"bill_id", "title", "conflict"}] for passed bills the new bill conflicts with.
    """
    if not analysis or not passed_bills:
        return []

    sections = "; ".join(s.get("heading", "") for s in analysis.get("sections", []))
    lines = [
        "A NEW bill is proposed. Identify which, if any, of the PREVIOUSLY PASSED bills it",
        "directly conflicts with — it contradicts, repeals, overrides, duplicates, or is",
        "legally incompatible with them. Be conservative: only flag genuine conflicts,",
        "and return an empty list if there are none. Describe each conflict in one sentence.",
        "",
        f"NEW BILL: {analysis.get('title', '')}",
        f"  summary: {analysis.get('plain_summary', '')}",
        f"  sections: {sections}",
        "",
        "PREVIOUSLY PASSED BILLS:",
    ]
    for b in passed_bills:
        lines.append(f"  [{b['id']}] {b.get('title', '')}: {b.get('summary', '')}")

    titles = {b["id"]: b.get("title", "") for b in passed_bills}
    data = llm.call_json(
        name="conflicts",
        model=config.CHECKER_MODEL,
        prompt="\n".join(lines),
        # bill_id is an enum of real passed-bill ids, so hallucinated ids are impossible.
        schema=obj({"conflicts": arr(obj({"bill_id": enum(list(titles)), "conflict": STR}))}),
        max_tokens=8000,
        client=client,
    )

    out = []
    for item in data.get("conflicts", []):
        bid = str(item.get("bill_id", "")).strip()
        if bid in titles:
            out.append({"bill_id": bid, "title": titles[bid], "conflict": str(item.get("conflict", "")).strip()})
    return out
