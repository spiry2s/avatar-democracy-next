"""
Quote checks and untrusted-text fences for bill analysis.

The model is asked for a verbatim quote on every section summary, red flag,
and beneficiary claim. After the call, each quote is accepted only if it
occurs in the source bill. A failed check is marked ungrounded. Callers
keep the claim in the API and leave it out of anything that votes.

A fence is not a sandbox. See the README limits.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


BILL_DATA_BEGIN = "<<<BILL_DATA>>>"
BILL_DATA_END = "<<<END_BILL_DATA>>>"
ANALYSIS_DATA_BEGIN = "<<<ANALYSIS_DATA>>>"
ANALYSIS_DATA_END = "<<<END_ANALYSIS_DATA>>>"

# A quote shorter than this is a fragment, not a ground. Length counts an
# allowlist after NFKC: letters, numbers, punctuation, and symbols.
# Combining marks, format characters, whitespace, variation selectors, and
# blank fillers do not count. Runs of the same punctuation or symbol collapse,
# and at least MIN_QUOTE_ALNUM of the counted characters must be letters or
# digits. The check runs only after the raw quote is found in the bill, so a
# short string that is not in the source is still reported as missing.
MIN_QUOTE_CHARS = 40
MIN_QUOTE_ALNUM = 20

# Blank characters that sit in letter or symbol categories and would otherwise
# count. Variation selectors are checked by code point as well as category.
_BLANK_FILLERS = frozenset(
    {
        "\u034f",  # combining grapheme joiner
        "\u115f",  # hangul choseong filler
        "\u1160",  # hangul jungseong filler
        "\u2800",  # braille pattern blank
        "\u3164",  # hangul filler
        "\uffa0",  # halfwidth hangul filler
        "\U00013441",  # egyptian hieroglyph full blank (Lo, but draws nothing)
        "\U00013442",  # egyptian hieroglyph half blank
    }
)

# Applied after NFC, and only for matching. NFKC stays on the length floor.
# Each value is the folded replacement. Ligatures expand; the rest are 1:1.
_MATCH_FOLD = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u00a0": " ",
    "\u202f": " ",
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}

TRUNCATION_MARKER = "\n\n[BILL TRUNCATED FOR LENGTH]"

SIGNIFICANCE = {"high", "medium", "low"}
SEVERITY = {"high", "medium", "low"}
FLAG_TYPES = {
    "buried_provision",
    "broad_delegation",
    "definition_change",
    "asymmetric_sunset",
    "hidden_funding",
    "cross_reference",
    "vague_language",
    "exemption",
    "retroactive_effect",
    "severability_risk",
}

_BENEFICIARY_GROUPS = ("benefits", "costs", "regulated")
_METADATA_ENUMS = {"complexity", "your_confidence"}

# A heading at the start of a line. "SECRETARY" does not match: a digit is required.
_HEADING = re.compile(
    r"(?im)^(?:sec(?:tion)?\.?\s+\d+[a-z0-9-]*|title\s+(?:[ivxlc]+|\d+))\b[^\n]*"
)

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


def shield_delimiters(text: str, begin: str, end: str) -> str:
    """Break fence tokens that appear inside untrusted text.

    The replacement is not the delimiter, so a search for `end` still finds
    only the fence the program adds.
    """
    text = text.replace(end, end.replace("<", "‹", 1))
    if begin != end:
        text = text.replace(begin, begin.replace("<", "‹", 1))
    return text


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fail(path: str) -> None:
    raise ValueError(f"Analysis JSON failed schema: {path}")


def _optional_int(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if not _is_int(value):
        _fail(f"{path} must be an integer")
    return value


def _enum(value: Any, allowed: set[str], path: str) -> str:
    if not isinstance(value, str):
        _fail(f"{path} must be a string")
    normalized = value.strip().lower()
    if normalized not in allowed:
        _fail(f"{path} must be one of {', '.join(sorted(allowed))}")
    return normalized


def _copy_quote_fields(item: dict[str, Any], path: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "quote" in item:
        if item["quote"] is not None and not isinstance(item["quote"], str):
            _fail(f"{path}.quote must be a string")
        out["quote"] = item["quote"] if isinstance(item["quote"], str) else ""
    if "quote_start" in item:
        out["quote_start"] = _optional_int(item["quote_start"], f"{path}.quote_start")
    if "quote_end" in item:
        out["quote_end"] = _optional_int(item["quote_end"], f"{path}.quote_end")
    return out


def validate_analysis(data: Any) -> dict[str, Any]:
    """Return a cleaned analysis object or raise ValueError.

    Required shape and field types are enforced. Unknown properties are
    dropped, so a model cannot smuggle extra fields downstream. A missing
    quote is allowed here and marked ungrounded later: the historical
    response shape still parses, and an absent quote is not treated as a
    finding.
    """
    if not isinstance(data, dict):
        _fail("top-level value must be an object")

    def req_str(key: str) -> str:
        if key not in data:
            _fail(f"missing {key}")
        if not isinstance(data[key], str):
            _fail(f"{key} must be a string")
        return data[key]

    title = req_str("title")
    plain_summary = req_str("plain_summary")

    if "sections" not in data or not isinstance(data["sections"], list):
        _fail("sections must be a list")
    sections: list[dict[str, Any]] = []
    for i, raw in enumerate(data["sections"]):
        if not isinstance(raw, dict):
            _fail(f"sections[{i}] must be an object")
        for key in ("section_id", "heading", "summary"):
            if key not in raw or not isinstance(raw[key], str):
                _fail(f"sections[{i}].{key} must be a string")
        section: dict[str, Any] = {
            "section_id": raw["section_id"],
            "heading": raw["heading"],
            "summary": raw["summary"],
        }
        if "significance" in raw and raw["significance"] is not None:
            section["significance"] = _enum(
                raw["significance"], SIGNIFICANCE, f"sections[{i}].significance"
            )
        if "significance_reason" in raw and raw["significance_reason"] is not None:
            if not isinstance(raw["significance_reason"], str):
                _fail(f"sections[{i}].significance_reason must be a string")
            section["significance_reason"] = raw["significance_reason"]
        section.update(_copy_quote_fields(raw, f"sections[{i}]"))
        # Unknown keys in `raw` are not copied.
        sections.append(section)

    if "beneficiaries" not in data or not isinstance(data["beneficiaries"], dict):
        _fail("beneficiaries must be an object")
    beneficiaries: dict[str, list[Any]] = {}
    for group in _BENEFICIARY_GROUPS:
        raw_items = data["beneficiaries"].get(group, [])
        if not isinstance(raw_items, list):
            _fail(f"beneficiaries.{group} must be a list")
        cleaned: list[Any] = []
        for i, item in enumerate(raw_items):
            path = f"beneficiaries.{group}[{i}]"
            if isinstance(item, str):
                cleaned.append(item)
                continue
            if not isinstance(item, dict):
                _fail(f"{path} must be a string or an object")
            if "claim" not in item or not isinstance(item["claim"], str):
                _fail(f"{path}.claim must be a string")
            claim: dict[str, Any] = {"claim": item["claim"]}
            claim.update(_copy_quote_fields(item, path))
            cleaned.append(claim)
        beneficiaries[group] = cleaned

    if "red_flags" not in data or not isinstance(data["red_flags"], list):
        _fail("red_flags must be a list")
    flags: list[dict[str, Any]] = []
    for i, raw in enumerate(data["red_flags"]):
        if not isinstance(raw, dict):
            _fail(f"red_flags[{i}] must be an object")
        for key in ("type", "section", "description", "severity"):
            if key not in raw:
                _fail(f"red_flags[{i}] missing {key}")
        flag = {
            "type": _enum(raw["type"], FLAG_TYPES, f"red_flags[{i}].type"),
            "section": raw["section"] if isinstance(raw["section"], str) else _fail(
                f"red_flags[{i}].section must be a string"
            ),
            "description": raw["description"] if isinstance(raw["description"], str) else _fail(
                f"red_flags[{i}].description must be a string"
            ),
            "severity": _enum(raw["severity"], SEVERITY, f"red_flags[{i}].severity"),
        }
        # _fail raises; the expressions above are strings when they return.
        flag.update(_copy_quote_fields(raw, f"red_flags[{i}]"))
        flags.append(flag)

    def req_str_list(key: str) -> list[str]:
        if key not in data or not isinstance(data[key], list):
            _fail(f"{key} must be a list")
        out: list[str] = []
        for i, item in enumerate(data[key]):
            if not isinstance(item, str):
                _fail(f"{key}[{i}] must be a string")
            out.append(item)
        return out

    comparable_laws = req_str_list("comparable_laws")
    open_questions = req_str_list("open_questions")

    if "metadata" not in data or not isinstance(data["metadata"], dict):
        _fail("metadata must be an object")
    metadata: dict[str, Any] = {}
    for key, value in data["metadata"].items():
        if not isinstance(key, str):
            _fail("metadata keys must be strings")
        if isinstance(value, bool) or value is None or isinstance(value, (str, int, float)):
            if key in _METADATA_ENUMS and isinstance(value, str):
                metadata[key] = _enum(value, SIGNIFICANCE, f"metadata.{key}")
            elif key in _METADATA_ENUMS and not isinstance(value, str):
                _fail(f"metadata.{key} must be a string")
            else:
                metadata[key] = value
        else:
            _fail(f"metadata.{key} must be a scalar")

    cleaned: dict[str, Any] = {
        "title": title,
        "plain_summary": plain_summary,
        "sections": sections,
        "beneficiaries": beneficiaries,
        "red_flags": flags,
        "comparable_laws": comparable_laws,
        "open_questions": open_questions,
        "metadata": metadata,
    }
    if "plain_summary_quote" in data and data["plain_summary_quote"] is not None:
        if not isinstance(data["plain_summary_quote"], str):
            _fail("plain_summary_quote must be a string")
        cleaned["plain_summary_quote"] = data["plain_summary_quote"]
    if "title_quote" in data and data["title_quote"] is not None:
        if not isinstance(data["title_quote"], str):
            _fail("title_quote must be a string")
        cleaned["title_quote"] = data["title_quote"]
    return cleaned


def locate_quote(
    quote: Any,
    source: str,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """Accept a quote only when those exact characters occur in `source`."""
    claimed_start = start if _is_int(start) else None
    claimed_end = end if _is_int(end) else None
    if not isinstance(quote, str) or not quote.strip():
        return {
            "quote": quote if isinstance(quote, str) else "",
            "quote_start": claimed_start,
            "quote_end": claimed_end,
            "grounded": False,
            "grounding_reason": "missing_quote",
        }

    candidates: list[str] = [quote]
    stripped = quote.strip()
    if stripped != quote:
        candidates.append(stripped)
    expanded: list[str] = []
    for candidate in candidates:
        expanded.append(candidate)
        if "\r\n" in candidate:
            expanded.append(candidate.replace("\r\n", "\n"))
        if "\n" in candidate:
            expanded.append(candidate.replace("\n", "\r\n"))

    seen: set[str] = set()
    for candidate in expanded:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (
            claimed_start is not None
            and claimed_end is not None
            and 0 <= claimed_start <= claimed_end <= len(source)
            and source[claimed_start:claimed_end] == candidate
        ):
            return {
                "quote": candidate,
                "quote_start": claimed_start,
                "quote_end": claimed_end,
                "grounded": True,
                "grounding_reason": "exact_offset",
            }
        idx = source.find(candidate)
        if idx >= 0:
            return {
                "quote": candidate,
                "quote_start": idx,
                "quote_end": idx + len(candidate),
                "grounded": True,
                "grounding_reason": "exact",
            }

    return {
        "quote": stripped,
        "quote_start": claimed_start,
        "quote_end": claimed_end,
        "grounded": False,
        "grounding_reason": "quote_not_in_source",
    }


def section_label_key(value: str) -> tuple[str, str] | None:
    """`(kind, number)` for a heading or a cited id. None when neither is present."""
    text = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
    match = re.search(r"\b(sec(?:tion)?|title)\s+(\d+[a-z]?|[ivxlc]+)\b", text)
    if not match:
        return None
    kind = "title" if match.group(1) == "title" else "sec"
    return (kind, match.group(2))


def bill_section_spans(source: str) -> list[dict[str, Any]]:
    """One span per heading, from that heading through the character before the next."""
    matches = list(_HEADING.finditer(source))
    spans = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        spans.append(
            {
                "start": start,
                "end": end,
                "heading": match.group(0),
                "key": section_label_key(match.group(0)),
            }
        )
    return spans


def resolve_cited_span(
    cited: str, spans: list[dict[str, Any]]
) -> tuple[str, tuple[int, int] | None]:
    """Map a cited section id onto exactly one heading span.

    Blank, "multiple", or any other whole-bill target is ambiguous. An id
    that matches no heading is unmatched. Two headings with the same number
    are ambiguous. All three are refused rather than searched at large.
    """
    if flag_targets_all(cited):
        return "section_ambiguous", None
    key = section_label_key(cited)
    if key is None:
        return "section_unmatched", None
    matches = [span for span in spans if span["key"] == key]
    if len(matches) == 1:
        return "ok", (matches[0]["start"], matches[0]["end"])
    if not matches:
        return "section_unmatched", None
    return "section_ambiguous", None


def _contained(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(lo <= start and end <= hi for lo, hi in ranges)


def _hits(source: str, candidate: str) -> list[tuple[int, int]]:
    found = []
    cursor = 0
    while True:
        index = source.find(candidate, cursor)
        if index < 0:
            return found
        found.append((index, index + len(candidate)))
        cursor = index + 1


def fold_for_match(text: str) -> tuple[str, list[int]]:
    """NFC, then the small match fold. `ends[i]` is the original end of `folded[:i]`.

    A match on the folded string at `[start, end)` covers `text[ends[start]:ends[end]]`.
    Ligatures expand to their letters and still map back onto the one original character.
    NFKC is not used here.
    """
    if not text:
        return "", [0]

    nfd_chars: list[str] = []
    nfd_span: list[tuple[int, int]] = []
    origin = 0
    for char in text:
        start = origin
        origin += 1
        for piece in unicodedata.normalize("NFD", char):
            nfd_chars.append(piece)
            nfd_span.append((start, origin))

    folded: list[str] = []
    ends = [0]
    index = 0
    count = len(nfd_chars)
    while index < count:
        nxt = index + 1
        while nxt < count and unicodedata.combining(nfd_chars[nxt]):
            nxt += 1
        cluster = "".join(nfd_chars[index:nxt])
        composed = unicodedata.normalize("NFC", cluster)
        if composed == cluster:
            pieces = [(nfd_chars[pos], nfd_span[pos]) for pos in range(index, nxt)]
        else:
            span = (nfd_span[index][0], nfd_span[nxt - 1][1])
            pieces = [(char, span) for char in composed]
        for char, (_start, end) in pieces:
            for folded_char in _MATCH_FOLD.get(char, char):
                folded.append(folded_char)
                ends.append(end)
        index = nxt
    return "".join(folded), ends


def _folded_hits(source: str, candidate: str, ends: list[int], folded_source: str) -> list[tuple[int, int]]:
    """Matches of `candidate` in `source`, as offsets into the original source."""
    folded_quote, _quote_ends = fold_for_match(candidate)
    if not folded_quote:
        return []
    found = []
    cursor = 0
    quote_len = len(folded_quote)
    while True:
        index = folded_source.find(folded_quote, cursor)
        if index < 0:
            return found
        found.append((ends[index], ends[index + quote_len]))
        cursor = index + 1


def _intersect(span: tuple[int, int], ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    start, end = span
    out = []
    for lo, hi in ranges:
        left, right = max(start, lo), min(end, hi)
        if left < right:
            out.append((left, right))
    return out


def _is_variation_selector(char: str) -> bool:
    code = ord(char)
    return 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def _counts_toward_quote(char: str) -> bool:
    """Allowlist: letter, number, punctuation, or symbol, and not a blank filler."""
    if char in _BLANK_FILLERS or _is_variation_selector(char):
        return False
    return unicodedata.category(char)[0] in "LNPS"


def visible_text(value: str) -> str:
    """Characters that count toward the quote floor and the title-in-quote check.

    NFKC runs first. Only letters, numbers, punctuation, and symbols remain.
    Combining marks, format characters, and whitespace drop out because they
    are outside that allowlist. Blank fillers and variation selectors drop
    even when their category would otherwise count. A run of the same
    punctuation or symbol character counts once, so dots or underscores
    cannot pad a short word.
    """
    normalized = unicodedata.normalize("NFKC", value)
    kept: list[str] = []
    for char in normalized:
        if not _counts_toward_quote(char):
            continue
        category = unicodedata.category(char)
        if category[0] in "PS" and kept and kept[-1] == char:
            continue
        kept.append(char)
    return "".join(kept)


def quote_meets_minimum(value: str) -> bool:
    """True when the quote has enough counted characters and enough letters or digits."""
    text = visible_text(value)
    alnum = sum(1 for char in text if unicodedata.category(char)[0] in "LN")
    return len(text) >= MIN_QUOTE_CHARS and alnum >= MIN_QUOTE_ALNUM


def title_inside_quote(title: str, quote: str) -> bool:
    """True when the title wording appears inside its quote, after normalization."""
    normalized_title = visible_text(title).casefold()
    normalized_quote = visible_text(quote).casefold()
    return bool(normalized_title) and normalized_title in normalized_quote


def _quote_candidates(quote: str) -> list[str]:
    stripped = quote.strip()
    seeds = [quote] if quote == stripped else [quote, stripped]
    expanded: list[str] = []
    seen: set[str] = set()
    for candidate in seeds:
        variants = [candidate]
        if "\r\n" in candidate:
            variants.append(candidate.replace("\r\n", "\n"))
        if "\n" in candidate:
            variants.append(candidate.replace("\n", "\r\n"))
        for variant in variants:
            if variant not in seen:
                seen.add(variant)
                expanded.append(variant)
    return expanded


def _ungrounded(quote: str, start: Any, end: Any, reason: str) -> dict[str, Any]:
    return {
        "quote": quote.strip() if isinstance(quote, str) else "",
        "quote_start": start if _is_int(start) else None,
        "quote_end": end if _is_int(end) else None,
        "grounded": False,
        "grounding_reason": reason,
    }


def accept_quote(
    quote: Any,
    source: str,
    start: Any = None,
    end: Any = None,
    *,
    covered: list[tuple[int, int]] | None = None,
    cited_section: str | None = None,
    spans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ground a claim quote.

    `cited_section` set: the quote must fall inside that heading's span.
    Unmatched and ambiguous ids are refused. `covered` is the character
    ranges actually sent to the model; a hit only in a truncated tail is
    not grounded. A hit that fails the length floor is not grounded either.
    The search folds NFC, curly quotes, dashes, non-breaking spaces, and a
    few ligatures, then maps the hit back onto the original characters.
    NFKC is not used for that search.
    """
    claimed_start = start if _is_int(start) else None
    claimed_end = end if _is_int(end) else None
    if not isinstance(quote, str) or not visible_text(quote):
        return _ungrounded(quote if isinstance(quote, str) else "", claimed_start, claimed_end, "missing_quote")

    if cited_section is not None:
        status, span = resolve_cited_span(cited_section, spans or [])
        if status != "ok" or span is None:
            return _ungrounded(quote, claimed_start, claimed_end, status)
        search_in = _intersect(span, covered if covered is not None else [(0, len(source))])
        section_span: tuple[int, int] | None = span
    else:
        search_in = list(covered) if covered is not None else [(0, len(source))]
        section_span = None

    folded_source, ends = fold_for_match(source)
    for candidate in _quote_candidates(quote):
        raw_hits = _hits(source, candidate)
        folded_hits = _folded_hits(source, candidate, ends, folded_source)
        chosen: tuple[int, int] | None = None
        how = "exact"
        if (
            claimed_start is not None
            and claimed_end is not None
            and (claimed_start, claimed_end) in raw_hits
            and _contained(claimed_start, claimed_end, search_in)
        ):
            chosen = (claimed_start, claimed_end)
            how = "exact_offset"
        else:
            for hit in folded_hits:
                if _contained(hit[0], hit[1], search_in):
                    chosen = hit
                    break
        if chosen is None:
            continue
        if not quote_meets_minimum(candidate):
            return _ungrounded(candidate, chosen[0], chosen[1], "quote_too_short")
        return {
            "quote": candidate,
            "quote_start": chosen[0],
            "quote_end": chosen[1],
            "grounded": True,
            "grounding_reason": how,
        }

    # No acceptable hit. Say why, using the stripped quote as the thing we looked for.
    stripped = quote.strip()
    anywhere = _folded_hits(source, stripped, ends, folded_source)
    if cited_section is not None and section_span is not None and any(
        _contained(hit[0], hit[1], [section_span]) for hit in anywhere
    ):
        return _ungrounded(quote, claimed_start, claimed_end, "quote_outside_analyzed_text")
    if cited_section is not None and anywhere:
        return _ungrounded(quote, claimed_start, claimed_end, "quote_not_in_section")
    if anywhere:
        return _ungrounded(quote, claimed_start, claimed_end, "quote_outside_analyzed_text")
    return _ungrounded(quote, claimed_start, claimed_end, "quote_not_in_source")


def _attach(item: dict[str, Any], located: dict[str, Any]) -> None:
    item["quote"] = located["quote"]
    item["quote_start"] = located["quote_start"]
    item["quote_end"] = located["quote_end"]
    item["grounded"] = located["grounded"]
    item["grounding_reason"] = located["grounding_reason"]
    item["withheld_from_avatar"] = not located["grounded"]


def _as_ranges(covered: list[tuple[int, int]] | list[list[int]] | None, source_len: int) -> list[tuple[int, int]]:
    if not covered:
        return [(0, source_len)]
    ranges = []
    for item in covered:
        if len(item) != 2:
            continue
        start, end = int(item[0]), int(item[1])
        if end > start:
            ranges.append((start, end))
    return ranges or [(0, source_len)]


def apply_grounding(
    data: dict[str, Any],
    source: str,
    covered_ranges: list[tuple[int, int]] | list[list[int]] | None = None,
) -> dict[str, Any]:
    """Mark every claim. Does not drop claims; sets withheld_from_avatar.

    Section summaries and red flags are checked inside the heading they cite.
    The plain summary, the title, and beneficiary claims have no section id,
    so they are checked against the analyzed text of the whole bill.
    """
    covered = _as_ranges(covered_ranges, len(source))
    spans = bill_section_spans(source)

    def in_section(cited: str, quote: Any, start: Any, end: Any) -> dict[str, Any]:
        return accept_quote(
            quote,
            source,
            start,
            end,
            covered=covered,
            cited_section=cited,
            spans=spans,
        )

    def in_document(quote: Any, start: Any = None, end: Any = None) -> dict[str, Any]:
        return accept_quote(quote, source, start, end, covered=covered)

    for section in data["sections"]:
        _attach(
            section,
            in_section(
                str(section.get("section_id", "")),
                section.get("quote", ""),
                section.get("quote_start"),
                section.get("quote_end"),
            ),
        )
    for flag in data["red_flags"]:
        _attach(
            flag,
            in_section(
                str(flag.get("section", "")),
                flag.get("quote", ""),
                flag.get("quote_start"),
                flag.get("quote_end"),
            ),
        )

    claims: list[dict[str, Any]] = []
    normalized: dict[str, list[str]] = {group: [] for group in _BENEFICIARY_GROUPS}
    for group in _BENEFICIARY_GROUPS:
        for item in data["beneficiaries"].get(group, []):
            if isinstance(item, str):
                claim_text, quote, start, end = item, "", None, None
            else:
                claim_text = item.get("claim", "")
                quote = item.get("quote", "")
                start = item.get("quote_start")
                end = item.get("quote_end")
            located = in_document(quote, start, end)
            normalized[group].append(claim_text)
            record = {"group": group, "claim": claim_text}
            _attach(record, located)
            claims.append(record)
    data["beneficiaries"] = normalized
    data["beneficiary_claims"] = claims

    summary_located = in_document(data.get("plain_summary_quote", ""))
    data["plain_summary_quote"] = summary_located["quote"]
    data["plain_summary_grounded"] = summary_located["grounded"]
    data["plain_summary_grounding_reason"] = summary_located["grounding_reason"]

    title_located = in_document(data.get("title_quote", ""))
    if title_located.get("grounded") and not title_inside_quote(
        str(data.get("title") or ""), str(title_located.get("quote") or "")
    ):
        title_located = {
            **title_located,
            "grounded": False,
            "grounding_reason": "title_not_in_quote",
        }
    data["title_quote"] = title_located["quote"]
    data["title_grounded"] = title_located["grounded"]
    data["title_grounding_reason"] = title_located["grounding_reason"]

    high = [
        flag
        for flag in data["red_flags"]
        if flag["grounded"] is False and flag.get("severity") == "high"
    ]
    data["grounding"] = {
        "policy": "verbatim_in_cited_section",
        "min_quote_chars": MIN_QUOTE_CHARS,
        "sections_total": len(data["sections"]),
        "sections_ungrounded": sum(1 for s in data["sections"] if s["grounded"] is False),
        "red_flags_total": len(data["red_flags"]),
        "red_flags_ungrounded": sum(1 for f in data["red_flags"] if f["grounded"] is False),
        "beneficiary_claims_total": len(claims),
        "beneficiary_claims_ungrounded": sum(1 for c in claims if c["grounded"] is False),
        "high_severity_ungrounded_flags": len(high),
        "plain_summary_grounded": data["plain_summary_grounded"],
        "title_grounded": data["title_grounded"],
    }
    return data


def _part_ranges(text: str) -> list[tuple[int, int]]:
    """Contiguous ranges covering the bill. Empty when fewer than two headings."""
    matches = list(_HEADING.finditer(text))
    if len(matches) < 2:
        return []
    starts = [0]
    for match in matches:
        if match.start() not in starts:
            starts.append(match.start())
    ranges: list[tuple[int, int]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        if end > start:
            ranges.append((start, end))
    return ranges if len(ranges) >= 2 else []


def split_on_headings(text: str) -> list[str]:
    """Split into preamble-plus-section chunks. Empty when fewer than two headings."""
    return [text[start:end] for start, end in _part_ranges(text)]


def plan_passes(text: str, limit: int) -> tuple[list[str], dict[str, Any]]:
    """Decide what text is sent to the model.

    A bill that fits in one pass is sent whole. A longer bill is split on
    section headings when every character can be sent. Otherwise the tail
    that does not fit is cut, the historical truncation marker is appended,
    and the result is blocking: callers must not treat the analysis as complete.
    """
    original = len(text)
    base = {
        "original_chars": original,
        "chunks": 1,
        "chunked": False,
        "truncated": False,
        "blocking": False,
        "analyzed_chars": original,
        "covered_ranges": [[0, original]],
        "message": "",
    }
    if original <= limit:
        return [text], base

    part_ranges = _part_ranges(text)
    if len(part_ranges) < 2:
        clipped = text[:limit] + TRUNCATION_MARKER
        base.update(
            {
                "truncated": True,
                "blocking": True,
                "analyzed_chars": limit,
                "covered_ranges": [[0, limit]],
                "message": (
                    "This bill is longer than the single-pass limit and has no section "
                    "headings to split on. The tail was not analyzed. The analysis is "
                    "incomplete, and the Avatar will not cast a yes or no vote."
                ),
            }
        )
        return [clipped], base

    chunks: list[str] = []
    current_parts: list[str] = []
    current_ranges: list[tuple[int, int]] = []
    covered: list[list[int]] = []
    any_truncated = False

    def flush() -> None:
        nonlocal current_parts, current_ranges
        if not current_parts:
            return
        chunks.append("".join(current_parts))
        covered.extend([[start, end] for start, end in current_ranges])
        current_parts = []
        current_ranges = []

    for start, end in part_ranges:
        part = text[start:end]
        if len(part) > limit:
            flush()
            chunks.append(part[:limit] + TRUNCATION_MARKER)
            covered.append([start, start + limit])
            any_truncated = True
            continue
        current_len = sum(range_end - range_start for range_start, range_end in current_ranges)
        if current_ranges and current_len + len(part) > limit:
            flush()
        current_parts.append(part)
        current_ranges.append((start, end))
    flush()

    analyzed = 0
    for chunk in chunks:
        if chunk.endswith(TRUNCATION_MARKER):
            analyzed += len(chunk) - len(TRUNCATION_MARKER)
        else:
            analyzed += len(chunk)

    info = {
        "original_chars": original,
        "chunks": len(chunks),
        "chunked": len(chunks) > 1,
        "analyzed_chars": analyzed,
        "covered_ranges": covered,
    }
    if any_truncated:
        info.update(
            {
                "truncated": True,
                "blocking": True,
                "message": (
                    "Part of this bill still exceeded the single-pass limit after "
                    "splitting on section headings. The omitted tail was not analyzed. "
                    "The analysis is incomplete, and the Avatar will not cast a yes or no vote."
                ),
            }
        )
    else:
        info.update(
            {
                "truncated": False,
                "blocking": False,
                "message": (
                    f"This bill was longer than one pass and was analyzed in {len(chunks)} "
                    "section chunks. No text was dropped."
                ),
            }
        )
    return chunks, info


def prompt_title(analysis: dict[str, Any]) -> str:
    """Title safe to place in a model prompt.

    An explicit `title_grounded: false` means the model-written title has no
    verified quote. Those prompts say "Bill under review" instead. A missing
    key is an older saved analysis and keeps its title.
    """
    if analysis.get("title_grounded") is False:
        return "Bill under review"
    title = analysis.get("title")
    if isinstance(title, str) and title.strip():
        return title
    return "Bill under review"


def downstream_sections(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Sections that were not explicitly marked ungrounded.

    A missing `grounded` key is left in place so older saved analyses still
    flow through. The analyzer always sets the key.
    """
    out = []
    for section in analysis.get("sections") or []:
        if isinstance(section, dict) and section.get("grounded") is not False:
            out.append(section)
    return out


def ungrounded_high_severity_flags(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    flags = []
    for flag in analysis.get("red_flags") or []:
        if not isinstance(flag, dict):
            continue
        if flag.get("grounded") is False and str(flag.get("severity", "")).lower() == "high":
            flags.append(flag)
    return flags


def _norm_section(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", value.lower())
    text = re.sub(r"\b(?:section|sec)\b", " ", text)
    return re.sub(r"\s+", "", text)


def flag_targets_all(section: str) -> bool:
    return section.strip().lower() in _ALL_TARGETS


def same_section(left_id: str, right_id: str) -> bool:
    """True when two section ids are the same label, ignoring 'Sec.' formatting.

    Unlike `flag_covers`, the words "multiple" and "whole_bill" do not match
    every id. This is for pairing a vote with a section, not for reading a flag.
    """
    if (left_id or "").strip() and (left_id or "").strip().lower() == (right_id or "").strip().lower():
        return True
    left = _norm_section(left_id or "")
    right = _norm_section(right_id or "")
    return bool(left) and left == right


def flag_covers(flag_section: str, section_id: str) -> bool:
    """True when a flag's section label refers to this section id."""
    raw = (flag_section or "").strip().lower()
    if raw in _ALL_TARGETS:
        return True
    return same_section(flag_section or "", section_id or "")


def section_block_reason(analysis: dict[str, Any], section_id: str) -> str:
    """Why this section must not receive a yes or no. Empty when it may."""
    reasons: list[str] = []
    matched: dict[str, Any] | None = None
    for section in analysis.get("sections") or []:
        if not isinstance(section, dict):
            continue
        sid = str(section.get("section_id", ""))
        if sid == section_id or same_section(sid, section_id):
            matched = section
            break
    if matched is not None and matched.get("grounded") is False:
        reasons.append("Section summary is ungrounded (no verbatim quote in the bill).")
    for flag in ungrounded_high_severity_flags(analysis):
        target = str(flag.get("section") or "")
        covers = flag_covers(target, section_id)
        if matched is not None:
            covers = covers or flag_covers(target, str(matched.get("section_id", "")))
        if covers:
            label = flag.get("type") or "flag"
            reasons.append(
                f"Ungrounded high-severity flag ({label}) refers to this section and was withheld."
            )
            break
    if not reasons:
        return ""
    return " ".join(reasons) + " The Avatar did not cast a yes or no."


def withheld_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    """Ungrounded material the API and UI should show, not hide."""
    sections = [
        str(section.get("section_id"))
        for section in analysis.get("sections") or []
        if isinstance(section, dict) and section.get("grounded") is False
    ]
    flags = []
    for flag in ungrounded_high_severity_flags(analysis):
        flags.append(
            {
                "type": flag.get("type", ""),
                "section": flag.get("section", ""),
                "description": flag.get("description", ""),
                "quote": flag.get("quote", ""),
                "grounding_reason": flag.get("grounding_reason", ""),
            }
        )
    return {"sections": sections, "high_severity_flags": flags}
