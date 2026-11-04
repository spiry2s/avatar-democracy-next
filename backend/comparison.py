"""
Compare two bill analyses without asking a third model to judge them.

`analyze_for_api` runs the primary analyzer and, when `COMPARISON_PROVIDER`
is set, the same `summarizer.analyze_bill` path on the comparison provider.
That path fences the bill (or sends a document block when the provider
accepts one), retries a bad schema once, and runs every quote through
`apply_grounding`. Matching is deterministic:

1. section ids (formatting ignored, same rules as `avatar._norm_key`)
2. quotes, when a quote is present and not marked `grounded: false`
3. token overlap of the claim text

`analyze_bill` marks each quote. A grounded quote can pair claims that do
not share a section id. A quote marked ungrounded is shown and is not a match.
Analyses that omit quotes still compare by section id and token overlap.

With no comparison provider, `analyze_for_api` returns `analyze_bill().to_dict()`
and does not add a key.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from backend import config, llm, summarizer


# A flag aimed at no single section. Same labels the grounding work uses
# (`grounding.flag_targets_all` / `_ALL_TARGETS`) so the two policies agree
# after that branch merges.
_ALL_TARGETS = {
    "",
    "multiple",
    "all",
    "whole",
    "whole_bill",
    "whole bill",
    "entire",
    "entire_bill",
    "entire bill",
    "*",
}

_TOKEN = re.compile(r"[a-z0-9]+")
# Whole words only. "notice" is not "not", and "niece" is not Polish "nie".
# "shall not" and "may not" are covered because "not" is its own word.
_NEGATIONS = frozenset({
    "not",
    "no",
    "never",
    "except",
    "unless",
    "without",
    "nie",    # Polish
    "ikke",   # Norwegian
    "ingen",  # Norwegian
})
# Thousands separators: comma, space, NBSP, and the narrow no-break space
# used in Polish and Norwegian grouping. A comma or dot before a non-group
# of digits is a decimal mark. The first alternative must win for "1,000"
# so it is not read as a decimal.
_GROUP_SEP = r"[, \u00a0\u202f]"
_NUMBER_RE = re.compile(
    r"(?<![a-z0-9])"
    r"([$€£])?\s*"
    r"(\d{1,3}(?:(?:" + _GROUP_SEP + r")\d{3})+(?:[.,]\d+)?"
    r"|\d+[.,]\d+"
    r"|\d+)"
    r"(\s*%)?",
    re.IGNORECASE,
)
_MODEL_DATE = re.compile(r"-(?:19|20)\d{2}-\d{2}-\d{2}$")
_MODEL_COMPACT_DATE = re.compile(r"-(?:19|20)\d{6}$")
_MODEL_TAG = re.compile(r"-(?:latest|preview|experimental)$")

# Pair two claims when the shorter one's words mostly appear in the longer.
TEXT_MATCH = 0.6
# Quotes are a tighter signal than a paraphrase of a summary.
QUOTE_MATCH = 0.8
# Below this, two paired summaries are reported as materially different.
MATERIAL_JACCARD = 0.5
# A long summary that contains a shorter one is not a material difference
# once the shorter side has this many words and this much of them appear.
CONTAINED_OVERLAP = 0.85
CONTAINED_MIN_TOKENS = 6

_POLICY_LABELS = {
    "abstain": (
        "If the models disagree on a high-severity red flag for a section, "
        "the Avatar abstains on that section and withholds the yes or no. "
        "Neither model overrides the other."
    ),
    "review": (
        "If the models disagree on a high-severity red flag for a section, "
        "review keeps the primary model's yes or no and only adds a human-review "
        "flag. That vote is still counted. Abstain is the setting that withholds it."
    ),
}

_GROUP_ORDER = {"benefits": 0, "costs": 1, "regulated": 2}


def comparison_configured() -> bool:
    """True when a comparison provider id is set. A blank value leaves analysis alone."""
    return bool((config.COMPARISON_PROVIDER or "").strip())


def norm_section_id(section_id: str) -> str:
    """Canonical section id. Matches `avatar._norm_key` and the grounding branch."""
    text = re.sub(r"[^a-z0-9]+", " ", str(section_id).lower())
    text = re.sub(r"\b(?:section|sec)\b", " ", text)
    return re.sub(r"\s+", "", text)


def targets_all_sections(section: str) -> bool:
    """True when a flag label is not a single section (blank, 'multiple', and so on)."""
    return str(section or "").strip().lower() in _ALL_TARGETS


def _prepare(text: str) -> str:
    """Lowercase and expand contractions so "don't" and "cannot" expose "not"."""
    lowered = str(text).lower().replace("\u2019", "'")
    lowered = lowered.replace("n't", " not ")
    return re.sub(r"\bcannot\b", " can not ", lowered)


def _norm_phrase(text: str) -> str:
    return " ".join(_TOKEN.findall(_prepare(text)))


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(_prepare(text)))


def _negations(text: str) -> frozenset[str]:
    return frozenset(token for token in _tokens(text) if token in _NEGATIONS)


def _strip_group_separators(body: str) -> str:
    for sep in (",", " ", "\u00a0", "\u202f"):
        body = body.replace(sep, "")
    return body


def _join_decimal(whole: str, frac: str) -> str:
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    if not frac:
        return whole
    return f"{whole}.{frac}"


def _number_key(prefix: str, body: str, percent: str | None) -> str:
    """One canonical number. 1,000, 1000, and '1 000' agree; 1,5 and 1.5 agree."""
    body = body.replace("\u00a0", " ").replace("\u202f", " ")
    if "," in body and "." in body:
        if body.rfind(",") > body.rfind("."):
            whole, frac = body.replace(".", "").rsplit(",", 1)
            whole = _strip_group_separators(whole)
        else:
            whole, frac = body.replace(",", "").replace(" ", "").rsplit(".", 1)
        number = _join_decimal(whole, frac)
    elif "," in body or " " in body:
        parts = re.split(r"[, ]", body)
        if all(len(part) == 3 and part.isdigit() for part in parts[1:]):
            number = ("".join(parts)).lstrip("0") or "0"
        else:
            whole, frac = parts[0], "".join(parts[1:])
            number = _join_decimal(whole, frac)
    elif "." in body:
        whole, frac = body.split(".", 1)
        number = _join_decimal(whole, frac)
    else:
        number = body.lstrip("0") or "0"
    if prefix:
        number = prefix + number
    if percent and "%" in percent:
        number += "%"
    return number


def _numbers(text: str) -> tuple[str, ...]:
    """Numbers in the order they appear. A reversed range is a different sequence."""
    keys = []
    for match in _NUMBER_RE.finditer(str(text).lower()):
        keys.append(_number_key(match.group(1) or "", match.group(2), match.group(3)))
    return tuple(keys)


def _polarity_agrees(left: str, right: str) -> bool:
    """False when negations differ or the number sequences differ.

    Numbers are an ordered sequence, not a set: "from 5 to 10" does not match
    "from 10 to 5". A negation or a number on only one side does not match.
    The check does not guess that "not" and "no" are the same word.
    """
    return _negations(left) == _negations(right) and _numbers(left) == _numbers(right)


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap(left: str, right: str) -> float:
    """Share of the shorter text's words that also appear in the longer one."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def texts_match(left: str, right: str) -> bool:
    """True when two claims are the same words, or the short one sits inside the long one.

    Negation and numbers are checked first. "shall" does not match "shall not",
    and "15" does not match "15%" or "16".
    """
    if not _polarity_agrees(left, right):
        return False
    a, b = _norm_phrase(left), _norm_phrase(right)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    shared = ta & tb
    if not shared:
        return False
    shorter = min(len(ta), len(tb))
    coeff = len(shared) / shorter
    if shorter <= 2:
        return coeff == 1.0
    return len(shared) >= 3 and coeff >= TEXT_MATCH


def quotes_match(left: str, right: str) -> bool:
    """True when two quotes are the same passage, or one excerpt contains the other.

    Containment is not enough when one quote adds a negation or a different
    number. "shall publish the names" does not match "shall not publish the names".
    """
    a, b = " ".join(str(left).split()), " ".join(str(right).split())
    if len(a) < 12 or len(b) < 12:
        return False
    if not _polarity_agrees(a, b):
        return False
    if a == b or a in b or b in a:
        return True
    if len(_tokens(a) & _tokens(b)) < 4:
        return False
    return _overlap(a, b) >= QUOTE_MATCH


def summaries_differ(left: str, right: str) -> bool:
    """True when paired section summaries are not the same claim in different words of similar length.

    A negation or a number on only one side is a difference even when the
    other words overlap heavily.
    """
    if not _polarity_agrees(left, right):
        return True
    if _norm_phrase(left) == _norm_phrase(right):
        return False
    if not str(left).strip() or not str(right).strip():
        return True
    if _jaccard(left, right) >= MATERIAL_JACCARD:
        return False
    a, b = _tokens(left), _tokens(right)
    shorter = min(len(a), len(b))
    if shorter >= CONTAINED_MIN_TOKENS and _overlap(left, right) >= CONTAINED_OVERLAP:
        return False
    return True


def _quote_of(item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("quote"), str):
        return item["quote"].strip()
    return ""


def _quote_grounded(item: Any) -> bool | None:
    """None when the analysis has no grounding mark. False only when explicitly ungrounded."""
    if isinstance(item, dict) and "grounded" in item:
        return item.get("grounded") is True
    return None


def _usable_quote(item: Any) -> str:
    """A quote safe to match on. An explicit ungrounded quote is not evidence."""
    if _quote_grounded(item) is False:
        return ""
    return _quote_of(item)


def _quote_fields(item: Any) -> dict[str, Any]:
    return {
        "quote": _quote_of(item),
        "quote_grounded": _quote_grounded(item),
    }


def _claim_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        value = item.get("claim")
        if isinstance(value, str):
            return value.strip()
    return ""


def _severity(flag: dict[str, Any]) -> str:
    return str(flag.get("severity") or "").strip().lower()


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _pair(
    left: list[Any],
    right: list[Any],
    steps: list[tuple[str, Callable[[Any, Any], float | None]]],
) -> list[tuple[int, int, str]]:
    """Greedy one-to-one pairing. Earlier steps win over later ones."""
    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs: list[tuple[int, int, str]] = []
    for name, score_of in steps:
        candidates: list[tuple[float, int, int]] = []
        for i, a in enumerate(left):
            if i in used_left:
                continue
            for j, b in enumerate(right):
                if j in used_right:
                    continue
                score = score_of(a, b)
                if score is None:
                    continue
                candidates.append((score, i, j))
        candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
        for _score, i, j in candidates:
            if i in used_left or j in used_right:
                continue
            used_left.add(i)
            used_right.add(j)
            pairs.append((i, j, name))
    return pairs


def _section_id_score(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    a = norm_section_id(str(left.get("section_id") or ""))
    b = norm_section_id(str(right.get("section_id") or ""))
    if a and a == b:
        return 1.0
    return None


def _quote_score(left: Any, right: Any) -> float | None:
    if quotes_match(_usable_quote(left), _usable_quote(right)):
        return 1.0
    return None


def _section_text_score(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    a = f"{left.get('heading') or ''} {left.get('summary') or ''}"
    b = f"{right.get('heading') or ''} {right.get('summary') or ''}"
    if texts_match(a, b):
        return _overlap(a, b)
    return None


def _flag_key_score(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    """Same flag type and the same section. Whole-bill labels only match each other."""
    a_type = str(left.get("type") or "").strip().lower()
    b_type = str(right.get("type") or "").strip().lower()
    if not a_type or a_type != b_type:
        return None
    a_section = str(left.get("section") or "")
    b_section = str(right.get("section") or "")
    a_all = targets_all_sections(a_section)
    b_all = targets_all_sections(b_section)
    if a_all and b_all:
        return 1.0
    if a_all or b_all:
        return None
    a_norm = norm_section_id(a_section)
    b_norm = norm_section_id(b_section)
    if a_norm and a_norm == b_norm:
        return 1.0
    return None


def _explicitly_ungrounded(item: Any) -> bool:
    """True only for a claim the quote check has already rejected."""
    return isinstance(item, dict) and item.get("grounded") is False


def _high_claims_agree(left: str, right: str) -> bool:
    """True only when the check is confident the two texts make the same claim.

    Overlap is not enough. The word sets have to match, and neither side may
    carry a negation or a number the other lacks. Anything else, including a
    paraphrase the checker cannot prove, is not agreement.
    """
    if not str(left).strip() and not str(right).strip():
        return True
    if not str(left).strip() or not str(right).strip():
        return False
    if not _polarity_agrees(left, right):
        return False
    return _tokens(left) == _tokens(right)


def _paired_high_disagrees(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when two high flags contradict, or the check cannot show they agree.

    Both the conclusion (description) and the quote are checked. The same
    quote with opposite conclusions is a disagreement.
    """
    if not _high_claims_agree(str(left.get("description") or ""), str(right.get("description") or "")):
        return True
    left_quote, right_quote = _quote_of(left), _quote_of(right)
    if not left_quote and not right_quote:
        return False
    return not _high_claims_agree(left_quote, right_quote)


def _flag_text_score(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    a = str(left.get("description") or "")
    b = str(right.get("description") or "")
    if texts_match(a, b):
        return _overlap(a, b)
    return None


def _claim_text_score(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    a, b = _claim_text(left), _claim_text(right)
    if texts_match(a, b):
        return _overlap(a, b)
    return None


def _section_view(section: dict[str, Any]) -> dict[str, Any]:
    view = {
        "section_id": str(section.get("section_id") or ""),
        "heading": str(section.get("heading") or ""),
        "summary": str(section.get("summary") or ""),
    }
    view.update(_quote_fields(section))
    return view


def _flag_view(flag: dict[str, Any]) -> dict[str, Any]:
    view = {
        "type": str(flag.get("type") or ""),
        "section": str(flag.get("section") or ""),
        "description": str(flag.get("description") or ""),
        "severity": _severity(flag),
    }
    view.update(_quote_fields(flag))
    return view


def _claim_view(item: dict[str, Any]) -> dict[str, Any]:
    view = {"claim": _claim_text(item)}
    view.update(_quote_fields(item))
    return view


def _iter_claims(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Prefer `beneficiary_claims` (quote-bearing records) when that list is present.

    The grounding branch flattens `beneficiaries` back to strings and keeps
    the quote on `beneficiary_claims`. Until that field exists, group strings
    and `{claim, quote}` objects in `beneficiaries` are the claims.
    """
    raw = analysis.get("beneficiary_claims")
    if isinstance(raw, list) and raw:
        claims: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    claims.append({"group": "", "claim": text})
            elif isinstance(item, dict) and _claim_text(item):
                claim = dict(item)
                claim["group"] = str(item.get("group") or "")
                claim["claim"] = _claim_text(item)
                claims.append(claim)
        if claims:
            return claims

    groups = analysis.get("beneficiaries") or {}
    claims = []
    if not isinstance(groups, dict):
        return claims
    for group, items in groups.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    claims.append({"group": str(group), "claim": text})
            elif isinstance(item, dict) and _claim_text(item):
                claim = dict(item)
                claim.setdefault("group", str(group))
                claim["group"] = str(claim.get("group") or group)
                claim["claim"] = _claim_text(item)
                claims.append(claim)
    return claims


def _high_item(
    flag: dict[str, Any],
    raised_by: str,
    kind: str,
    *,
    primary_section: str,
    comparison_section: str,
    matched_by: str = "",
    other_severity: str = "",
) -> dict[str, Any]:
    if kind == "unmatched":
        # A blank or "multiple" label is not one section. Hold every vote.
        targets_all = targets_all_sections(str(flag.get("section") or ""))
    else:
        targets_all = targets_all_sections(primary_section) or targets_all_sections(comparison_section)
    item = {
        "section": str(flag.get("section") or ""),
        "primary_section": primary_section,
        "comparison_section": comparison_section,
        "targets_all": targets_all,
        "raised_by": raised_by,
        "type": str(flag.get("type") or ""),
        "severity": "high",
        "description": str(flag.get("description") or ""),
        "kind": kind,
        "matched_by": matched_by,
        "other_severity": other_severity,
    }
    item.update(_quote_fields(flag))
    return item


def _compare_sections(
    primary: list[dict[str, Any]], comparison: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pairs = _pair(
        primary,
        comparison,
        [
            ("section_id", _section_id_score),
            ("quote", _quote_score),
            ("text_overlap", _section_text_score),
        ],
    )
    used_left = {i for i, _, _ in pairs}
    used_right = {j for _, j, _ in pairs}
    different: list[dict[str, Any]] = []
    for i, j, how in pairs:
        left, right = primary[i], comparison[j]
        if not summaries_differ(str(left.get("summary") or ""), str(right.get("summary") or "")):
            continue
        row = {
            "section_id": str(left.get("section_id") or ""),
            "comparison_section_id": str(right.get("section_id") or ""),
            "matched_by": how,
            "primary_heading": str(left.get("heading") or ""),
            "comparison_heading": str(right.get("heading") or ""),
            "primary_summary": str(left.get("summary") or ""),
            "comparison_summary": str(right.get("summary") or ""),
            "primary_quote": _quote_of(left),
            "comparison_quote": _quote_of(right),
            "primary_quote_grounded": _quote_grounded(left),
            "comparison_quote_grounded": _quote_grounded(right),
        }
        different.append(row)
    only_primary = [_section_view(primary[i]) for i in range(len(primary)) if i not in used_left]
    only_comparison = [
        _section_view(comparison[j]) for j in range(len(comparison)) if j not in used_right
    ]
    return only_primary, only_comparison, different


def _compare_flags(
    primary: list[dict[str, Any]], comparison: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    # An ungrounded flag is not evidence the other model agreed. Leave it
    # unpaired so a high one is reported as raised by only one side.
    left_map = [i for i, flag in enumerate(primary) if not _explicitly_ungrounded(flag)]
    right_map = [j for j, flag in enumerate(comparison) if not _explicitly_ungrounded(flag)]
    local_pairs = _pair(
        [primary[i] for i in left_map],
        [comparison[j] for j in right_map],
        [
            ("section_id", _flag_key_score),
            ("quote", _quote_score),
            ("text_overlap", _flag_text_score),
        ],
    )
    pairs = [(left_map[i], right_map[j], how) for i, j, how in local_pairs]
    used_left = {i for i, _, _ in pairs}
    used_right = {j for _, j, _ in pairs}
    high: list[dict[str, Any]] = []
    for i, j, how in pairs:
        left, right = primary[i], comparison[j]
        left_sev, right_sev = _severity(left), _severity(right)
        if left_sev == "high" and right_sev == "high":
            if _paired_high_disagrees(left, right):
                item = _high_item(
                    left,
                    "primary",
                    "content",
                    primary_section=str(left.get("section") or ""),
                    comparison_section=str(right.get("section") or ""),
                    matched_by=how,
                )
                item["description"] = (
                    "Primary: "
                    + str(left.get("description") or "")
                    + " Comparison: "
                    + str(right.get("description") or "")
                )
                item["comparison_description"] = str(right.get("description") or "")
                item["comparison_quote"] = _quote_of(right)
                item["comparison_quote_grounded"] = _quote_grounded(right)
                high.append(item)
            continue
        if left_sev != "high" and right_sev != "high":
            continue
        if left_sev == "high":
            raised, other, flag = "primary", right_sev, left
        else:
            raised, other, flag = "comparison", left_sev, right
        high.append(
            _high_item(
                flag,
                raised,
                "severity",
                primary_section=str(left.get("section") or ""),
                comparison_section=str(right.get("section") or ""),
                matched_by=how,
                other_severity=other,
            )
        )
    only_primary = []
    for i, flag in enumerate(primary):
        if i in used_left:
            continue
        only_primary.append(_flag_view(flag))
        if _severity(flag) == "high":
            high.append(
                _high_item(
                    flag,
                    "primary",
                    "unmatched",
                    primary_section=str(flag.get("section") or ""),
                    comparison_section="",
                )
            )
    only_comparison = []
    for j, flag in enumerate(comparison):
        if j in used_right:
            continue
        only_comparison.append(_flag_view(flag))
        if _severity(flag) == "high":
            high.append(
                _high_item(
                    flag,
                    "comparison",
                    "unmatched",
                    primary_section="",
                    comparison_section=str(flag.get("section") or ""),
                )
            )
    return only_primary, only_comparison, high


def _compare_claims(
    primary: list[dict[str, Any]], comparison: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pairs = _pair(
        primary,
        comparison,
        [
            ("quote", _quote_score),
            ("text_overlap", _claim_text_score),
        ],
    )
    used_left = {i for i, _, _ in pairs}
    used_right = {j for _, j, _ in pairs}
    conflicts: list[dict[str, Any]] = []
    for i, j, how in pairs:
        left_group = str(primary[i].get("group") or "")
        right_group = str(comparison[j].get("group") or "")
        if left_group == right_group:
            continue
        row = {
            "claim": _claim_text(primary[i]) or _claim_text(comparison[j]),
            "primary_group": left_group,
            "comparison_group": right_group,
            "matched_by": how,
            "primary_quote": _quote_of(primary[i]),
            "comparison_quote": _quote_of(comparison[j]),
            "primary_quote_grounded": _quote_grounded(primary[i]),
            "comparison_quote_grounded": _quote_grounded(comparison[j]),
        }
        conflicts.append(row)

    buckets: dict[str, dict[str, Any]] = {}

    def bucket(group: str) -> dict[str, Any]:
        return buckets.setdefault(
            group, {"group": group, "only_primary": [], "only_comparison": []}
        )

    for i, item in enumerate(primary):
        if i not in used_left:
            bucket(str(item.get("group") or ""))["only_primary"].append(_claim_view(item))
    for j, item in enumerate(comparison):
        if j not in used_right:
            bucket(str(item.get("group") or ""))["only_comparison"].append(_claim_view(item))
    differences = [
        row for row in buckets.values() if row["only_primary"] or row["only_comparison"]
    ]
    differences.sort(key=lambda row: (_GROUP_ORDER.get(row["group"], 9), row["group"]))
    return differences, conflicts


def build_report(
    primary: dict[str, Any],
    comparison: dict[str, Any],
    *,
    primary_info: dict[str, str],
    comparison_info: dict[str, str],
    policy: str,
) -> dict[str, Any]:
    """Structured disagreement report. Pure: no model call."""
    policy = policy if policy in _POLICY_LABELS else "abstain"
    sections_only_primary, sections_only_comparison, sections_different = _compare_sections(
        _dicts(primary.get("sections")), _dicts(comparison.get("sections"))
    )
    flags_only_primary, flags_only_comparison, high = _compare_flags(
        _dicts(primary.get("red_flags")), _dicts(comparison.get("red_flags"))
    )
    beneficiary_differences, beneficiary_group_conflicts = _compare_claims(
        _iter_claims(primary), _iter_claims(comparison)
    )
    return {
        "enabled": True,
        "policy": policy,
        "policy_label": _POLICY_LABELS[policy],
        "primary": {
            "provider": primary_info.get("provider") or "",
            "model": primary_info.get("model") or "",
        },
        "comparison": {
            "provider": comparison_info.get("provider") or "",
            "model": comparison_info.get("model") or "",
        },
        "matching": ["section_id", "quote", "text_overlap"],
        "note": (
            "Matched by section id, then by quote when a quote is present and "
            "not marked ungrounded, then by token overlap. Agreement compares "
            "the wording, not the meaning, so a paraphrase is a disagreement "
            "and the Avatar abstains more often than a reader might expect. "
            "No third model judges this. Two models can still share training "
            "data and biases."
        ),
        "sections_only_primary": sections_only_primary,
        "sections_only_comparison": sections_only_comparison,
        "sections_materially_different": sections_different,
        "red_flags_only_primary": flags_only_primary,
        "red_flags_only_comparison": flags_only_comparison,
        "high_severity_disagreements": high,
        "beneficiary_differences": beneficiary_differences,
        "beneficiary_group_conflicts": beneficiary_group_conflicts,
    }


def _flag_hits(item: dict[str, Any], section_id: str) -> bool:
    if item.get("targets_all"):
        return True
    vote = norm_section_id(section_id)
    if not vote:
        return False
    for label in (
        item.get("section"),
        item.get("primary_section"),
        item.get("comparison_section"),
    ):
        if label and norm_section_id(str(label)) == vote:
            return True
    return False


def _policy_note(policy: str, hits: list[dict[str, Any]], prior: str) -> str:
    bits: list[str] = []
    for item in hits:
        who = "the primary model" if item.get("raised_by") == "primary" else "the comparison model"
        label = item.get("type") or "a high-severity flag"
        section = item.get("section") or "the bill"
        if item.get("kind") == "content":
            bits.append(
                f"{label} on {section}: the conclusions contradict, or the check could not show they agree"
            )
        elif item.get("kind") == "severity":
            other = item.get("other_severity") or "not high"
            bits.append(f"{who} rates {label} on {section} as high and the other model rates it {other}")
        elif item.get("targets_all"):
            bits.append(f"{who} raised {label} on {section or 'the whole bill'}")
        else:
            bits.append(f"{who} raised {label} on {section}")
    detail = "; ".join(bits)
    prior_label = prior or "abstain"
    if policy == "review":
        return (
            f"The models disagree on a high-severity red flag ({detail}). "
            f"The primary model voted {prior_label}. That yes or no is kept and "
            "only flagged for human review. It is not withheld."
        )
    return (
        f"The models disagree on a high-severity red flag ({detail}). "
        f"The primary model voted {prior_label}; the Avatar abstained instead, "
        "so neither model overrode the other."
    )


def apply_vote_policy(section_votes: list[Any], analysis: dict[str, Any]) -> set[str]:
    """Hold sections where the models disagree on a high-severity red flag.

    Returns the section ids whose position was forced to abstain. No-op when
    the analysis has no comparison report, so a vote with the checker role
    alone is unchanged.

    `abstain` (default) replaces the position. `review` keeps the primary
    position, marks `needs_human_review`, and lowers confidence. The recorded
    position in review mode still counts in a chamber tally; abstain does not
    add a yes or no. Abstain is the conservative default for that reason.
    """
    report = analysis.get("model_comparison") if isinstance(analysis, dict) else None
    if not isinstance(report, dict) or not report.get("enabled"):
        return set()
    policy = str(report.get("policy") or "abstain").strip().lower()
    if policy not in _POLICY_LABELS:
        policy = "abstain"
    items = [item for item in report.get("high_severity_disagreements") or [] if isinstance(item, dict)]
    if not items:
        return set()
    abstained: set[str] = set()
    for vote in section_votes:
        hits = [item for item in items if _flag_hits(item, getattr(vote, "section_id", ""))]
        if not hits:
            continue
        prior = str(getattr(vote, "position", "") or "")
        vote.comparison_prior_position = prior
        vote.comparison_note = _policy_note(policy, hits, prior)
        vote.confidence = "low"
        if policy == "review":
            vote.needs_human_review = True
            vote.comparison_abstained = False
        else:
            vote.position = "abstain"
            vote.comparison_abstained = True
            vote.needs_human_review = False
            abstained.add(str(vote.section_id))
    return abstained


def without_abstained(section_ids: Any, abstained_ids: set[str]) -> Any:
    """Drop forced-abstain ids from the model's would-pass list.

    Returns `section_ids` unchanged when nothing was forced, including a
    non-list the model happened to return.
    """
    if not abstained_ids:
        return section_ids
    if not isinstance(section_ids, list):
        return section_ids
    blocked = {norm_section_id(sid) for sid in abstained_ids}
    blocked.update(abstained_ids)
    kept = []
    for sid in section_ids:
        text = str(sid)
        if text in abstained_ids or (norm_section_id(text) and norm_section_id(text) in blocked):
            continue
        kept.append(sid)
    return kept


def normalize_model_id(model: str, provider_id: str) -> str:
    """Lowercase, drop a provider prefix, and drop a trailing date or -latest tag.

    A bare version fragment such as the final "-6" in "claude-sonnet-4-6" stays.
    Stripping that would treat different Claude ids as one model.
    """
    name = str(model or "").strip().lower()
    provider_id = str(provider_id or "").strip().lower()
    for prefix in (
        f"{provider_id}/" if provider_id else "",
        f"{provider_id}:" if provider_id else "",
        "openai/",
        "openai:",
        "anthropic/",
        "anthropic:",
    ):
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
            break
    while name:
        updated = _MODEL_DATE.sub("", name)
        updated = _MODEL_COMPACT_DATE.sub("", updated)
        updated = _MODEL_TAG.sub("", updated)
        if updated == name or not updated:
            break
        name = updated
    return name


def model_relationship(
    primary_model: str,
    primary_provider: str,
    comparison_model: str,
    comparison_provider: str,
) -> str:
    """'same', 'ambiguous', or 'different'.

    'same' is refused. 'ambiguous' is a warning: one id is the other plus a
    suffix that is not a date or a -latest tag, so it might be a different model.
    Different vendors are 'different' even when the model strings match.
    """
    if str(primary_provider or "").strip().lower() != str(comparison_provider or "").strip().lower():
        return "different"
    left = normalize_model_id(primary_model, primary_provider)
    right = normalize_model_id(comparison_model, comparison_provider)
    if left == right:
        return "same"
    longer, shorter = (left, right) if len(left) >= len(right) else (right, left)
    if shorter and longer.startswith(shorter) and longer[len(shorter):].startswith("-"):
        return "ambiguous"
    return "different"


def analyze_for_api(bill_text: str) -> dict[str, Any]:
    """Primary analysis, plus a comparison report when that provider is configured.

    Both passes call `summarizer.analyze_bill`, so they share the prompt, the
    truncation, and the JSON parse. A comparison failure is raised rather than
    hidden: a missing key or a bad response must not look like agreement.
    """
    comparison_provider = None
    if comparison_configured():
        # Fail before the primary call when the comparison role is misconfigured.
        comparison_provider = llm.get_provider("comparison")
        primary_id = (config.LLM_PROVIDER or "anthropic").strip().lower() or "anthropic"
        primary_model = llm.model_for(primary_id, "primary")
        relation = model_relationship(
            primary_model,
            primary_id,
            comparison_provider.model,
            comparison_provider.provider_id,
        )
        if relation == "same":
            raise llm.LLMConfigError(
                "The comparison provider and model are the same as the primary "
                f"({primary_id} / {normalize_model_id(primary_model, primary_id)}). "
                "A date suffix or a -latest tag does not make it a different model. "
                "That is not an independent check. "
                "Set COMPARISON_PROVIDER or COMPARISON_MODEL to a different model."
            )
        self_comparison_warning = ""
        if relation == "ambiguous":
            self_comparison_warning = (
                f"The comparison model {comparison_provider.model!r} looks like the "
                f"primary model {primary_model!r} with an extra suffix. That suffix "
                "is not a date or a -latest tag, so this might be a different model "
                "and the check still ran. It may not be an independent check."
            )
    else:
        self_comparison_warning = ""
    primary = summarizer.analyze_bill(bill_text).to_dict()
    if comparison_provider is None:
        return primary
    other = summarizer.analyze_bill(bill_text, provider=comparison_provider)
    policy = config.COMPARISON_VOTE_POLICY
    if policy not in _POLICY_LABELS:
        policy = "abstain"
    primary_provider = (config.LLM_PROVIDER or "anthropic").strip().lower() or "anthropic"
    report = build_report(
        primary,
        other.to_dict(),
        primary_info={
            "provider": primary_provider,
            "model": llm.model_for(primary_provider, "primary"),
        },
        comparison_info={
            "provider": comparison_provider.provider_id,
            "model": comparison_provider.model,
        },
        policy=policy,
    )
    if self_comparison_warning:
        report["self_comparison_warning"] = self_comparison_warning
    primary["model_comparison"] = report
    return primary
