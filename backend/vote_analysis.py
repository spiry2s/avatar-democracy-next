"""Check a client-supplied analysis before the Avatar votes on it.

``POST /api/avatar/vote`` does not fetch or analyze a bill. The browser sends
the analysis, including one handed off from the Analyze page. That object is
untrusted. ``validate_analysis`` keeps the schema the analyzer already uses
and drops unknown fields. This module then puts back the grounding, truncation,
and comparison marks the vote engine reads, and refuses a payload that would
slip past ``AVATAR_MAX_BILL_CHARS``.

A single string longer than the bill cap is refused. The text that would be
placed in the vote prompt is refused when it is longer than twice the cap.
The whole JSON tree is refused when its strings add up to more than six times
the cap, which leaves room for a comparison report that repeats the analysis.
"""

from __future__ import annotations

from typing import Any

from backend import config
from backend.grounding import _fail, validate_analysis

# Six times the bill cap covers the analysis plus a comparison report that
# repeats its quotes. The prompt itself is held to two times the cap.
VOTE_ANALYSIS_RAW_FACTOR = 6
VOTE_ANALYSIS_PROMPT_FACTOR = 2
VOTE_ANALYSIS_MAX_DEPTH = 12
VOTE_ANALYSIS_MAX_ITEMS = 2000
VOTE_ANALYSIS_MAX_NODES = 100_000
VOTE_REASON_MAX = 80
VOTE_COMPARISON_TEXT_MAX = 500
VOTE_COMPARISON_ITEMS_MAX = 200
VOTE_TRUNCATION_MESSAGE_MAX = 2000
VOTE_COUNT_MAX = 50_000_000

_GROUPS = frozenset({"benefits", "costs", "regulated"})
_ITEM_GROUNDED = ("grounded",)
_TOP_BOOLS = ("title_grounded", "plain_summary_grounded")
_TOP_REASONS = ("title_grounding_reason", "plain_summary_grounding_reason")
_COMPARISON_STRINGS = (
    "section",
    "primary_section",
    "comparison_section",
    "raised_by",
    "type",
    "kind",
    "other_severity",
)


class AnalysisRejected(Exception):
    """The posted analysis will not be voted on. ``detail`` is safe to show."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def vote_analysis_too_large(limit: int) -> str:
    """The 413 message. It names the cap and not the rejected text."""
    return (
        f"This analysis is larger than the {limit}-character bill limit. "
        "It was not sent for a vote."
    )


def vote_prompt_chars(analysis: dict[str, Any]) -> int:
    """Characters from ``analysis`` that the vote prompt would include.

    This follows ``avatar._build_bill_block``: an explicit ``grounded: false``
    summary, quote, or flag is left out, and a missing ``grounded`` key is
    included. Beneficiary claims are included only when that list says the
    claim is grounded. Without the list, the beneficiary strings are included.
    """
    total = 0

    def add(value: Any) -> None:
        nonlocal total
        if isinstance(value, str):
            total += len(value)

    if analysis.get("title_grounded") is not False:
        add(analysis.get("title"))
    if analysis.get("plain_summary_grounded") is not False:
        add(analysis.get("plain_summary"))

    for section in analysis.get("sections") or []:
        if not isinstance(section, dict):
            continue
        add(section.get("section_id"))
        if section.get("grounded") is False:
            continue
        add(section.get("heading"))
        add(section.get("summary"))
        add(section.get("quote"))
        add(section.get("significance"))

    for flag in analysis.get("red_flags") or []:
        if not isinstance(flag, dict) or flag.get("grounded") is False:
            continue
        add(flag.get("severity"))
        add(flag.get("type"))
        add(flag.get("section"))
        add(flag.get("description"))
        add(flag.get("quote"))

    claims = analysis.get("beneficiary_claims")
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, dict) and claim.get("grounded") is True:
                add(claim.get("claim"))
    else:
        benef = analysis.get("beneficiaries") or {}
        if isinstance(benef, dict):
            for group in ("benefits", "costs", "regulated"):
                items = benef.get(group) or []
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, str):
                        add(item)
                    elif isinstance(item, dict):
                        add(item.get("claim"))
    return total


def accept_vote_analysis(data: Any) -> dict[str, Any]:
    """Return a vote-ready analysis, or raise.

    ``ValueError`` is a schema miss (``validate_analysis`` and the marks this
    function checks). ``AnalysisRejected`` with status 413 is over the bill cap.
    Unknown fields are not returned.
    """
    if not isinstance(data, dict):
        _fail("top-level value must be an object")
    limit = config.max_bill_chars()
    _reject_oversized_tree(data, limit)
    cleaned = validate_analysis(data)
    _attach_vote_marks(cleaned, data, limit)
    if vote_prompt_chars(cleaned) > limit * VOTE_ANALYSIS_PROMPT_FACTOR:
        raise AnalysisRejected(vote_analysis_too_large(limit), 413)
    return cleaned


def _reject_oversized_tree(data: dict[str, Any], limit: int) -> None:
    raw_limit = limit * VOTE_ANALYSIS_RAW_FACTOR
    total = 0
    seen = 0
    stack: list[tuple[Any, int]] = [(data, 0)]
    while stack:
        node, depth = stack.pop()
        seen += 1
        if depth > VOTE_ANALYSIS_MAX_DEPTH:
            _fail("nesting is too deep")
        if seen > VOTE_ANALYSIS_MAX_NODES:
            raise AnalysisRejected(vote_analysis_too_large(limit), 413)
        if isinstance(node, str):
            if len(node) > limit:
                raise AnalysisRejected(vote_analysis_too_large(limit), 413)
            total += len(node)
            if total > raw_limit:
                raise AnalysisRejected(vote_analysis_too_large(limit), 413)
            continue
        if isinstance(node, dict):
            if len(node) > VOTE_ANALYSIS_MAX_ITEMS:
                raise AnalysisRejected(vote_analysis_too_large(limit), 413)
            for key, value in node.items():
                if not isinstance(key, str):
                    _fail("object keys must be strings")
                if len(key) > VOTE_REASON_MAX:
                    _fail("object key is too long")
                total += len(key)
                if total > raw_limit:
                    raise AnalysisRejected(vote_analysis_too_large(limit), 413)
                stack.append((value, depth + 1))
            continue
        if isinstance(node, list):
            if len(node) > VOTE_ANALYSIS_MAX_ITEMS:
                raise AnalysisRejected(vote_analysis_too_large(limit), 413)
            for item in node:
                stack.append((item, depth + 1))
            continue
        if isinstance(node, bool) or node is None or isinstance(node, (int, float)):
            continue
        _fail("unsupported value")


def _attach_vote_marks(cleaned: dict[str, Any], raw: dict[str, Any], limit: int) -> None:
    for key in _TOP_BOOLS:
        flag = _optional_bool(raw, key, key)
        if flag is not None:
            cleaned[key] = flag
    for key in _TOP_REASONS:
        reason = _optional_reason(raw, key, key)
        if reason is not None:
            cleaned[key] = reason

    raw_sections = raw.get("sections")
    if isinstance(raw_sections, list):
        for index, section in enumerate(cleaned["sections"]):
            source = raw_sections[index] if isinstance(raw_sections[index], dict) else {}
            _attach_item(section, source, f"sections[{index}]")
    raw_flags = raw.get("red_flags")
    if isinstance(raw_flags, list):
        for index, flag in enumerate(cleaned["red_flags"]):
            source = raw_flags[index] if isinstance(raw_flags[index], dict) else {}
            _attach_item(flag, source, f"red_flags[{index}]")

    if "beneficiary_claims" in raw:
        cleaned["beneficiary_claims"] = _clean_claims(raw.get("beneficiary_claims"), limit)
    truncation = _clean_truncation(raw)
    if truncation is not None:
        cleaned["truncation"] = truncation
    comparison = _clean_comparison(raw)
    if comparison is not None:
        cleaned["model_comparison"] = comparison


def _attach_item(item: dict[str, Any], source: dict[str, Any], path: str) -> None:
    for key in _ITEM_GROUNDED:
        flag = _optional_bool(source, key, f"{path}.{key}")
        if flag is not None:
            item[key] = flag
    reason = _optional_reason(source, "grounding_reason", f"{path}.grounding_reason")
    if reason is not None:
        item["grounding_reason"] = reason


def _optional_bool(source: dict[str, Any], key: str, path: str) -> bool | None:
    if key not in source or source[key] is None:
        return None
    if not isinstance(source[key], bool):
        _fail(f"{path} must be a boolean")
    return source[key]


def _optional_reason(source: dict[str, Any], key: str, path: str) -> str | None:
    if key not in source or source[key] is None:
        return None
    if not isinstance(source[key], str):
        _fail(f"{path} must be a string")
    if len(source[key]) > VOTE_REASON_MAX:
        _fail(f"{path} is too long")
    return source[key]


def _clean_claims(raw: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        _fail("beneficiary_claims must be a list")
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        path = f"beneficiary_claims[{index}]"
        if not isinstance(item, dict):
            _fail(f"{path} must be an object")
        group = item.get("group")
        if group not in _GROUPS:
            _fail(f"{path}.group must be benefits, costs, or regulated")
        claim = item.get("claim")
        if not isinstance(claim, str):
            _fail(f"{path}.claim must be a string")
        if len(claim) > limit:
            raise AnalysisRejected(vote_analysis_too_large(limit), 413)
        record: dict[str, Any] = {"group": group, "claim": claim}
        _attach_item(record, item, path)
        cleaned.append(record)
    return cleaned


def _clean_truncation(raw: dict[str, Any]) -> dict[str, Any] | None:
    if "truncation" not in raw:
        return None
    value = raw["truncation"]
    if value is None:
        return None
    if not isinstance(value, dict):
        _fail("truncation must be an object")
    out: dict[str, Any] = {}
    for key in ("truncated", "blocking", "chunked"):
        flag = _optional_bool(value, key, f"truncation.{key}")
        if flag is not None:
            out[key] = flag
    if "message" in value and value["message"] is not None:
        message = value["message"]
        if not isinstance(message, str):
            _fail("truncation.message must be a string")
        if len(message) > VOTE_TRUNCATION_MESSAGE_MAX:
            _fail("truncation.message is too long")
        out["message"] = message
    for key in ("original_chars", "analyzed_chars", "chunks"):
        if key not in value or value[key] is None:
            continue
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int):
            _fail(f"truncation.{key} must be an integer")
        if number < 0 or number > VOTE_COUNT_MAX:
            _fail(f"truncation.{key} must be an integer")
        out[key] = number
    return out


def _clean_comparison(raw: dict[str, Any]) -> dict[str, Any] | None:
    if "model_comparison" not in raw:
        return None
    report = raw["model_comparison"]
    if report is None:
        return None
    if not isinstance(report, dict):
        _fail("model_comparison must be an object")
    enabled = report.get("enabled")
    if enabled is None:
        enabled = False
    if not isinstance(enabled, bool):
        _fail("model_comparison.enabled must be a boolean")
    if enabled is not True:
        return {"enabled": False}

    policy = report.get("policy", "abstain")
    if not isinstance(policy, str) or len(policy) > 32:
        _fail("model_comparison.policy must be a string")
    status = report.get("status", "")
    if not isinstance(status, str) or len(status) > 32:
        _fail("model_comparison.status must be a string")
    reason = report.get("unavailable_reason", "")
    if not isinstance(reason, str) or len(reason) > VOTE_COMPARISON_TEXT_MAX:
        _fail("model_comparison.unavailable_reason must be a string")

    items = report.get("high_severity_disagreements", [])
    if not isinstance(items, list):
        _fail("model_comparison.high_severity_disagreements must be a list")
    if len(items) > VOTE_COMPARISON_ITEMS_MAX:
        _fail("model_comparison.high_severity_disagreements is too long")
    cleaned_items: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        path = f"model_comparison.high_severity_disagreements[{index}]"
        if not isinstance(item, dict):
            _fail(f"{path} must be an object")
        kept: dict[str, Any] = {}
        if "targets_all" in item and item["targets_all"] is not None:
            if not isinstance(item["targets_all"], bool):
                _fail(f"{path}.targets_all must be a boolean")
            kept["targets_all"] = item["targets_all"]
        for key in _COMPARISON_STRINGS:
            if key not in item or item[key] is None:
                continue
            text = item[key]
            if not isinstance(text, str):
                _fail(f"{path}.{key} must be a string")
            if len(text) > VOTE_COMPARISON_TEXT_MAX:
                _fail(f"{path}.{key} is too long")
            kept[key] = text
        cleaned_items.append(kept)
    return {
        "enabled": True,
        "policy": policy.strip().lower() or "abstain",
        "status": status.strip().lower(),
        "unavailable_reason": reason,
        "high_severity_disagreements": cleaned_items,
    }
