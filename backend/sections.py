"""Section-id helpers shared by the Avatar, the checker, and the Proxy Chamber."""

from __future__ import annotations

import re
from typing import Any

WHOLE_BILL = "whole_bill"


def section_ids(analysis: dict[str, Any]) -> list[str]:
    """The bill's section ids, de-duplicated, in order. A bill the analyst
    couldn't split is voted as one section, WHOLE_BILL."""
    ids = [str(s.get("section_id", "")).strip() for s in analysis.get("sections", [])]
    ids = [i for i in dict.fromkeys(ids) if i]
    return ids or [WHOLE_BILL]


def norm_key(section_id: str) -> str:
    """Canonicalize a section id to its distinctive part so differing formats
    still match: 'Sec. 101', 'sec_101', 'Section 101', '101' -> '101'."""
    s = re.sub(r"[^a-z0-9]+", " ", str(section_id).lower())
    s = re.sub(r"\b(?:section|sec)\b", " ", s)
    return re.sub(r"\s+", "", s)


def lookup(mapping: dict[str, Any], section_id: str, default: Any = None) -> Any:
    """Find section_id in mapping exactly, else by normalized key."""
    if section_id in mapping:
        return mapping[section_id]
    want = norm_key(section_id)
    for k, v in mapping.items():
        if norm_key(k) == want:
            return v
    return default


def norm_name(name: str) -> str:
    """Normalize a person's name for matching: lowercase, no punctuation or
    honorifics. 'Dr. James Hansen' and 'james hansen' -> 'james hansen'."""
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name).lower())
    s = re.sub(r"\b(?:dr|sen|senator|rep|prof|mr|mrs|ms|hon)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()
