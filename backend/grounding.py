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
# blank fillers do not count. Punctuation whose Unicode name matches *FILLER
# (including the numbered FILLER-1 form) does not count either. Runs of the
# same punctuation or symbol collapse, and at least MIN_QUOTE_ALNUM of the
# counted characters must be letters or digits. The check runs only after the
# raw quote is found in the bill, so a short string that is not in the source
# is still reported as missing.
MIN_QUOTE_CHARS = 40
MIN_QUOTE_ALNUM = 20
# Title quotes only. A short official name such as a Norwegian statute title
# is under the section floor. Section quotes keep MIN_QUOTE_CHARS.
MIN_TITLE_CHARS = 20
MIN_TITLE_ALNUM = 12

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
    # Long s + t is a historical s, so the quote's modern spelling is "st".
    "\ufb05": "st",
    "\ufb06": "st",
    # Dutch digraph. Not a letter in French or Norwegian.
    "\u0132": "IJ",
    "\u0133": "ij",
}

# U+0152 Œ and U+0153 œ are intentionally absent. French treats œ as a letter
# (cœur, œuvre), and Unicode does not decompose it. Norwegian romanizes ø,
# a different letter, as "oe". Folding œ to oe would blur both.

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
# US headings stay case-insensitive and at column 0, which is how govinfo prints
# them. Norwegian § / §§ and Polish Art. / ust. / pkt. may be indented. Art. is
# case-sensitive so a wrapped cross-reference ("art. 11 ustawy") is not a heading.
# A singular § line is a heading when it has a period ("§ 6."), a capitalized
# title ("§ 1 Formål"), or the marker alone ends the line. A bare "§ N" with
# no period and no title is not a heading when the previous line does not end
# a sentence and the next line continues in lowercase. "§§" is a cross-reference
# in Norwegian text: "§§ 3" alone on a line does not open a section. "§§ 4 til 6."
# still does, because the line is the heading, not a wrapped number.
# Bare "1." and "1)" lines are not in this pattern. They are ust./pkt. only inside
# an Art. span, so a Norwegian numbered list does not open a section.
_US_HEADING = (
    r"(?i:sec(?:tion)?\.?\s+\d+[a-z0-9-]*|title\s+(?:[ivxlc]+|\d+))\b[^\n]*"
)
# Optional "N" or "N til M" / "N og M" after the section sign.
_PARA_RANGE = r"[ \t]*\d+[a-zA-Z]?(?:[ \t]*(?:til|og|–|-)[ \t]*\d+[a-zA-Z]?)*"
# Plural §§ opens a section only with a period or a capitalized title on that
# line. A line that is only "§§ 3" is a wrapped cross-reference.
_PLURAL_HEADING = rf"§§(?:{_PARA_RANGE})?[ \t]*(?:\.[^\n]*|(?=[A-ZÆØÅ])[^\n]*)"
# Singular § also allows the marker to end the line. A bare "§ N" is removed
# later when the surrounding lines show it is a continuation.
_SINGULAR_HEADING = rf"§{_PARA_RANGE}[ \t]*(?:\.[^\n]*|(?=[A-ZÆØÅ])[^\n]*|(?=\n|$))"
_PARA_HEADING = rf"(?P<paragraph_line>(?:{_PLURAL_HEADING}|{_SINGULAR_HEADING}))"
_BARE_SINGULAR_SECTION = re.compile(r"^§[ \t]*\d+[a-zA-Z]?[ \t]*$")
_SENTENCE_END = re.compile(r"[.?!][\"'»”)\]]*$")
_HEADING = re.compile(
    rf"(?m)^(?:(?P<us>{_US_HEADING})"
    rf"|[ \t]*(?:{_PARA_HEADING}"
    rf"|(?P<art>Art\.\s*\d+[a-zA-Z]?\b[^\n]*)"
    rf"|(?P<ust>ust\.\s*\d+[a-zA-Z]?\b[^\n]*)"
    rf"|(?P<pkt>pkt\.?\s*\d+[a-zA-Z]?\b[^\n]*)))"
)
_BARE_HEADING = re.compile(
    r"(?m)^[ \t]*(?:(?P<bare_ust>\d+[a-zA-Z]?\.(?:[ \t][^\n]*)?)"
    r"|(?P<bare_pkt>\d+[a-zA-Z]?\)[^\n]*))"
)
# Top-level headings are peers. ust. and pkt. nest inside them, so an article
# span runs through its ustępy and a quote cited to the article can sit in one.
_HEADING_LEVEL = {
    "title": 20,
    "sec": 20,
    "paragraph": 20,
    "paragraphs": 20,
    "art": 20,
    "ust": 30,
    "pkt": 40,
}
_MARKER = re.compile(
    r"(?P<paragraphs>§§)"
    r"|(?P<paragraph>§)"
    r"|(?P<art>\bArt\.)"
    r"|(?P<ust>\bust\.)"
    r"|(?P<pkt>\bpkt\.?)"
    r"|(?P<sec>\bsec(?:tion)?\.?)"
    r"|(?P<title>\btitle\b)",
    re.IGNORECASE,
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


def _number_at(text: str, pos: int, *, roman: bool) -> str | None:
    if roman:
        match = re.match(r"\s*(\d+[a-z]?|[ivxlc]+)\b", text[pos:], re.IGNORECASE)
    else:
        match = re.match(r"\s*(\d+[a-z]?)\b", text[pos:], re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower()


def section_label_key(value: str) -> tuple[tuple[str, str], ...] | None:
    """Path of `(kind, number)` pairs for a heading or a cited id.

    `Sec. 2(a)` is `(('sec', '2'),)`. `Art. 1 ust. 2` keeps both levels.
    `§ 1a` is a paragraph. `§§` is a different kind, so it does not satisfy
    a citation of `§ 1`. None when no marker is present.
    """
    text = str(value)
    path: list[tuple[str, str]] = []
    for match in _MARKER.finditer(text):
        kind_name = match.lastgroup or ""
        if kind_name == "paragraphs":
            kind = "paragraphs"
        elif kind_name == "paragraph":
            kind = "paragraph"
        elif kind_name == "art":
            kind = "art"
        elif kind_name == "ust":
            kind = "ust"
        elif kind_name == "pkt":
            kind = "pkt"
        elif kind_name == "sec":
            kind = "sec"
        else:
            kind = "title"
        number = _number_at(text, match.end(), roman=(kind == "title"))
        if kind == "paragraphs" and number is None:
            path.append((kind, ""))
            continue
        if number is None:
            continue
        path.append((kind, number))
    if not path:
        return None
    return tuple(path)


def _heading_kind(match: re.Match[str]) -> str:
    for name in ("us", "paragraph_line", "paragraphs", "paragraph", "art", "ust", "pkt", "bare_ust", "bare_pkt"):
        if match.groupdict().get(name):
            return name
    return ""


def _leading_key(heading: str) -> tuple[tuple[str, str], ...] | None:
    """The marker at the start of a heading line, ignoring later cross-references.

    `Art. 2. ... o których mowa w art. 11 ustawy` is Art. 2, not Art. 11.
    A citation such as `Art. 1 ust. 2` still uses `section_label_key`, which
    reads every marker, because that string is the path and not a sentence.
    """
    text = heading.strip()
    match = _MARKER.search(text)
    if match is None or text[: match.start()].strip():
        return None
    kind_name = match.lastgroup or ""
    if kind_name == "paragraphs":
        kind = "paragraphs"
    elif kind_name == "paragraph":
        kind = "paragraph"
    elif kind_name == "art":
        kind = "art"
    elif kind_name == "ust":
        kind = "ust"
    elif kind_name == "pkt":
        kind = "pkt"
    elif kind_name == "sec":
        kind = "sec"
    else:
        kind = "title"
    number = _number_at(text, match.end(), roman=(kind == "title"))
    if kind == "paragraphs" and number is None:
        return ((kind, ""),)
    if number is None:
        return None
    return ((kind, number),)


def _own_key(kind: str, heading: str) -> tuple[tuple[str, str], ...] | None:
    if kind == "bare_ust":
        match = re.match(r"\s*(\d+[a-zA-Z]?)\.", heading)
        if not match:
            return None
        return (("ust", match.group(1).lower()),)
    if kind == "bare_pkt":
        match = re.match(r"\s*(\d+[a-zA-Z]?)\)", heading)
        if not match:
            return None
        return (("pkt", match.group(1).lower()),)
    return _leading_key(heading)


def _explicit_heading(match: re.Match[str]) -> dict[str, Any] | None:
    kind = _heading_kind(match)
    own = _own_key(kind, match.group(0))
    if not own:
        return None
    level_kind = own[-1][0]
    return {
        "start": match.start(),
        "heading": match.group(0).strip(),
        "kind": kind,
        "own": own,
        "level": _HEADING_LEVEL[level_kind],
    }


def _last_nonempty_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return None


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def _is_wrapped_section_reference(source: str, match: re.Match[str]) -> bool:
    """A bare ``§ N`` continued from the previous line and into lowercase text.

    ``§ 6.`` and ``§ 1 Formål`` stay headings. ``§§ 3`` is not matched as a
    heading at all. This catches a singular marker with no period and no title
    when the sentence did not end above it and the next line carries on in
    lowercase.
    """
    if not _BARE_SINGULAR_SECTION.fullmatch(match.group(0).strip()):
        return False
    previous = _last_nonempty_line(source[: match.start()])
    if previous is None or _SENTENCE_END.search(previous):
        return False
    following = _first_nonempty_line(source[match.end() :])
    return following is not None and following[:1].islower()


def _heading_matches(source: str) -> list[re.Match[str]]:
    return [
        match
        for match in _HEADING.finditer(source)
        if not _is_wrapped_section_reference(source, match)
    ]


def bill_section_spans(source: str) -> list[dict[str, Any]]:
    """One span per heading.

    A top-level heading runs until the next top-level heading, so it contains
    the ust. and pkt. under it. A ust. runs until the next ust. or a higher
    heading. Bare `1.` / `1)` lines count only inside an `Art.` span.
    """
    explicit: list[dict[str, Any]] = []
    for match in _heading_matches(source):
        item = _explicit_heading(match)
        if item is not None:
            explicit.append(item)

    art_ranges: list[tuple[int, int]] = []
    for index, item in enumerate(explicit):
        if item["kind"] != "art":
            continue
        end = len(source)
        for nxt in explicit[index + 1 :]:
            if nxt["level"] <= item["level"]:
                end = nxt["start"]
                break
        art_ranges.append((item["start"], end))

    explicit_starts = {item["start"] for item in explicit}
    bare: list[dict[str, Any]] = []
    for match in _BARE_HEADING.finditer(source):
        if match.start() in explicit_starts:
            continue
        if not any(lo <= match.start() < hi for lo, hi in art_ranges):
            continue
        item = _explicit_heading(match)
        if item is not None:
            bare.append(item)

    raw = sorted(explicit + bare, key=lambda item: item["start"])
    stack: list[tuple[int, tuple[tuple[str, str], ...]]] = []
    spans: list[dict[str, Any]] = []
    for item in raw:
        level = item["level"]
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent: tuple[tuple[str, str], ...] = stack[-1][1] if stack else ()
        full = parent + item["own"]
        stack.append((level, full))
        spans.append(
            {
                "start": item["start"],
                "end": len(source),
                "heading": item["heading"],
                "key": full,
                "level": level,
            }
        )
    for index, span in enumerate(spans):
        for nxt in spans[index + 1 :]:
            if nxt["level"] <= span["level"]:
                span["end"] = nxt["start"]
                break
    return spans


def _keys_match(
    heading_key: tuple[tuple[str, str], ...] | None,
    cited_key: tuple[tuple[str, str], ...],
) -> bool:
    if not heading_key:
        return False
    if heading_key == cited_key:
        return True
    # "ust. 2" matches the ust. 2 inside whichever article, and two of them
    # are ambiguous. An empty §§ number must not match §§ 4.
    if len(cited_key) < len(heading_key) and heading_key[-len(cited_key) :] == cited_key:
        return all(number for _kind, number in cited_key)
    return False


def resolve_cited_span(
    cited: str, spans: list[dict[str, Any]]
) -> tuple[str, tuple[int, int] | None]:
    """Map a cited section id onto exactly one heading span.

    Blank, "multiple", or any other whole-bill target is ambiguous. An id
    that matches no heading is unmatched. Two headings with the same number
    are ambiguous. All three are refused rather than searched at large.
    A quote cited to Art. 1 may sit in an ust. of that article. A quote
    cited to ust. 2 has to sit in that ustęp.
    """
    if flag_targets_all(cited):
        return "section_ambiguous", None
    key = section_label_key(cited)
    if key is None:
        return "section_unmatched", None
    matches = [span for span in spans if _keys_match(span.get("key"), key)]
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


def _is_match_space(char: str) -> bool:
    """Whitespace that collapses for matching. A zero-width space does not."""
    if char in "\t\n\r\v\f\u2028\u2029":
        return True
    return unicodedata.category(char) == "Zs"


def _is_lower_letter(char: str) -> bool:
    return bool(char) and char.islower() and unicodedata.category(char).startswith("L")


def _is_letter(char: str) -> bool:
    return bool(char) and unicodedata.category(char).startswith("L")


def _collapse_match_units(
    units: list[tuple[str, int, str]],
) -> tuple[str, list[int]]:
    """Collapse whitespace, drop soft hyphens, and join a safe line-break hyphen.

    `units` are `(folded char, original end, original slice)`. The slice is
    empty for the tail of a ligature expansion. A joined hyphen keeps the
    original characters inside the mapped span: the letters on either side
    still point at their own offsets, so the slice from the first letter
    through the last includes the hyphen and the newline.
    """
    out_chars: list[str] = []
    ends = [0]
    index = 0
    count = len(units)
    while index < count:
        char, end, orig = units[index]
        if char == "\u00ad" or orig == "\u00ad":
            index += 1
            continue
        if _line_hyphen_break(units, index, out_chars):
            index += 1
            while index < count and _unit_is_space(units[index]):
                index += 1
            continue
        if _unit_is_space((char, end, orig)):
            nxt = index
            while nxt < count and _unit_is_space(units[nxt]):
                nxt += 1
            out_chars.append(" ")
            ends.append(units[nxt - 1][1])
            index = nxt
            continue
        out_chars.append(char)
        ends.append(end)
        index += 1
    return "".join(out_chars), ends


def _unit_is_space(unit: tuple[str, int, str]) -> bool:
    char, _end, orig = unit
    if _is_match_space(char):
        return True
    return bool(orig) and all(_is_match_space(piece) for piece in orig)


def _unit_has_break(unit: tuple[str, int, str]) -> bool:
    char, _end, orig = unit
    return any(piece in "\n\r\u2028\u2029" for piece in (char, orig))


def _line_hyphen_break(
    units: list[tuple[str, int, str]],
    index: int,
    out_chars: list[str],
) -> bool:
    """Join `word-\\nword` only for a lowercase syllable split.

    The hyphen must be an ASCII hyphen-minus at the end of the line, not an
    en dash or em dash that the match fold turned into "-". A space before
    the line break is not a split. The left piece is at least four lowercase
    letters and is not the tail of a capitalized word. The right piece is two
    to four lowercase letters. That covers a PDF split such as
    "regional-\\nnego" and refuses "cat-\\ndog", "state-\\nowned",
    "pre-\\nexisting", and "Well-\\nknown".
    """
    char, _end, orig = units[index]
    if char != "-" or orig != "-":
        return False
    run = 0
    cursor = len(out_chars) - 1
    while cursor >= 0 and _is_lower_letter(out_chars[cursor]):
        run += 1
        cursor -= 1
    if run < 4:
        return False
    if cursor >= 0 and _is_letter(out_chars[cursor]):
        return False
    nxt = index + 1
    if nxt >= len(units) or not _unit_is_space(units[nxt]):
        return False
    # The hyphen has to end the line. A space before the break is "word- word",
    # which is not a PDF syllable split. Indentation after the break is allowed.
    saw_break = False
    while nxt < len(units) and _unit_is_space(units[nxt]):
        if _unit_has_break(units[nxt]):
            saw_break = True
        elif not saw_break:
            return False
        nxt += 1
    if not saw_break or nxt >= len(units):
        return False
    right = 0
    while nxt < len(units) and _is_lower_letter(units[nxt][0]):
        right += 1
        nxt += 1
    return 2 <= right <= 4


def fold_for_match(text: str) -> tuple[str, list[int]]:
    """NFC, the small match fold, then whitespace collapse.

    `ends[i]` is the original end of `folded[:i]`. A match on the folded
    string at `[start, end)` covers `text[ends[start]:ends[end]]`. Ligatures
    expand to their letters and still map back onto the one original character.
    Runs of spaces, tabs, newlines, and other Unicode spaces (category Zs,
    including NBSP) become one space. Soft hyphens are dropped. A line-break
    hyphen is joined only by `_line_hyphen_break`. A zero-width space is left
    in place. NFKC is not used here.
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

    units: list[tuple[str, int, str]] = []
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
        for char, (start, end) in pieces:
            original = text[start:end]
            expanded = _MATCH_FOLD.get(char, char)
            if len(expanded) == 1:
                units.append((expanded, end, original))
            else:
                # The first expanded letter carries the original character.
                # The rest map to an empty slice so the end offset stays put.
                for offset, folded_char in enumerate(expanded):
                    units.append((folded_char, end, original if offset == 0 else ""))
        index = nxt
    return _collapse_match_units(units)


# A Sejm journal citation and nothing else. "(Dz. U." then an optional
# "z YYYY r.", then optional poz. and/or nr number lists joined by ",",
# " i ", or " oraz ", then an optional ", z późn. zm.", then ")".
# "nie", amounts, art./ust./pkt, and any other word fail the pattern, so they
# are not skipped. Dz. Urz., M.P., and "(t.j. Dz. U. ...)" are not this form.
_NUM_LIST = r"\d+(?:\s*(?:,\s*|\s+i\s+|\s+oraz\s+)\d+)*"
_POZ_LIST = rf"poz\.\s*{_NUM_LIST}"
_NR_LIST = rf"nr\s+{_NUM_LIST}"
_CITATION_LISTS = (
    rf"(?:{_POZ_LIST}(?:\s*(?:,\s*|\s+i\s+|\s+oraz\s+){_NR_LIST})?"
    rf"|{_NR_LIST}(?:\s*(?:,\s*|\s+i\s+|\s+oraz\s+){_POZ_LIST})?)"
)
_JOURNAL_PAREN = re.compile(
    r"\(\s*[Dd]z\.\s*[Uu]\.\s*"
    r"(?:z\s+\d{4}\s+r\.\s+)?"
    rf"(?:{_CITATION_LISTS}\s*)?"
    r"(?:,\s*z\s+późn\.\s*zm\.\s*)?"
    r"\)"
)


def _find_folded(folded: str, ends: list[int], needle: str) -> list[tuple[int, int]]:
    found = []
    cursor = 0
    needle_len = len(needle)
    while True:
        index = folded.find(needle, cursor)
        if index < 0:
            return found
        found.append((ends[index], ends[index + needle_len]))
        cursor = index + 1


def _omit_folded_span(folded: str, ends: list[int], start: int, end: int) -> tuple[str, list[int]]:
    """Drop one folded span and collapse the space it leaves. Ends stay original offsets."""
    kept: list[str] = []
    kept_ends = [ends[0]]
    for index, char in enumerate(folded):
        if start <= index < end:
            continue
        kept.append(char)
        kept_ends.append(ends[index + 1])
    out: list[str] = []
    out_ends = [kept_ends[0]]
    for pos, char in enumerate(kept):
        if char == " " and out and out[-1] == " ":
            out_ends[-1] = kept_ends[pos + 1]
            continue
        out.append(char)
        out_ends.append(kept_ends[pos + 1])
    return "".join(out), out_ends


def _folded_hits(source: str, candidate: str, ends: list[int], folded_source: str) -> list[tuple[int, int]]:
    """Matches of `candidate` in `source`, as offsets into the original source.

    A quote that does not itself contain a journal citation may cross one
    citation-only `(Dz. U. ...)` parenthetical. A second citation is not
    removed in the same attempt, so two gaps cannot be bridged.
    """
    folded_quote, _quote_ends = fold_for_match(candidate)
    if not folded_quote:
        return []
    found = _find_folded(folded_source, ends, folded_quote)
    if found or _JOURNAL_PAREN.search(folded_quote):
        return found
    hits: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in _JOURNAL_PAREN.finditer(folded_source):
        stripped, stripped_ends = _omit_folded_span(
            folded_source, ends, match.start(), match.end()
        )
        for hit in _find_folded(stripped, stripped_ends, folded_quote):
            if hit not in seen:
                seen.add(hit)
                hits.append(hit)
    return hits


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


def _name_tail_is_filler(name: str) -> bool:
    """True when the last word of a Unicode name matches *FILLER.

    "GAP FILLER" matches. The numbered gap fillers match too: "FILLER-1"
    and "FILLER-2" do not end in FILLER, but they are the same characters.
    """
    tail = name.rsplit(" ", 1)[-1]
    if tail == "FILLER":
        return True
    prefix = "FILLER-"
    return tail.startswith(prefix) and tail[len(prefix) :].isdigit()


def _is_punctuation_filler(char: str) -> bool:
    """Punctuation that fills a gap rather than contributing text.

    Letters named FILLER are already on the blank list. This is the same
    name rule for category P: DEVANAGARI GAP FILLER, KAWI PUNCTUATION SPACE
    FILLER, BHAIKSUKI GAP FILLER-1, and the rest.
    """
    if unicodedata.category(char)[:1] != "P":
        return False
    name = unicodedata.name(char, "")
    return bool(name) and _name_tail_is_filler(name)


def _counts_toward_quote(char: str) -> bool:
    """Allowlist: letter, number, punctuation, or symbol, and not a filler."""
    if char in _BLANK_FILLERS or _is_variation_selector(char):
        return False
    if unicodedata.category(char)[0] not in "LNPS":
        return False
    return not _is_punctuation_filler(char)


def visible_text(value: str) -> str:
    """Characters that count toward the quote length floor.

    NFKC runs first. Only letters, numbers, punctuation, and symbols remain.
    Combining marks, format characters, and whitespace drop out because they
    are outside that allowlist. Blank fillers, variation selectors, and
    punctuation whose names match *FILLER drop even when their category
    would otherwise count. A run of the same punctuation or symbol character
    counts once, so dots or underscores cannot pad a short word.
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


def _length_counts(value: str) -> tuple[int, int]:
    text = visible_text(value)
    alnum = sum(1 for char in text if unicodedata.category(char)[0] in "LN")
    return len(text), alnum


def quote_meets_minimum(value: str) -> bool:
    """True when the quote has enough counted characters and enough letters or digits."""
    length, alnum = _length_counts(value)
    return length >= MIN_QUOTE_CHARS and alnum >= MIN_QUOTE_ALNUM


def title_quote_meets_minimum(value: str) -> bool:
    """Shorter floor for a title quote. Section quotes do not use this."""
    length, alnum = _length_counts(value)
    return length >= MIN_TITLE_CHARS and alnum >= MIN_TITLE_ALNUM


def title_inside_quote(title: str, quote: str) -> bool:
    """True when the title wording appears inside its quote.

    Both sides go through the quote matcher: NFC, then `_MATCH_FOLD`.
    Case is folded after that, so capitalization alone does not separate a
    title from the bill's wording. The length floor's NFKC allowlist is not
    applied here.
    """
    normalized_title = fold_for_match(title)[0].casefold()
    normalized_quote = fold_for_match(quote)[0].casefold()
    return bool(normalized_title.strip()) and normalized_title in normalized_quote


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
    for_title: bool = False,
) -> dict[str, Any]:
    """Ground a claim quote.

    `cited_section` set: the quote must fall inside that heading's span.
    Unmatched and ambiguous ids are refused. `covered` is the character
    ranges actually sent to the model; a hit only in a truncated tail is
    not grounded. A hit that fails the length floor is not grounded either.
    The search folds NFC, curly quotes, dashes, and a few ligatures (ff, fi,
    fl, ffi, ffl, st, and ij). It does not fold œ. Runs of whitespace,
    including newlines and Unicode spaces, collapse to one space. A soft
    hyphen is dropped. A line-break hyphen joins only under the syllable
    rule in `fold_for_match`. A hit is mapped back onto the original
    characters, so the span still contains the newline or hyphen that was
    folded away. NFKC is not used for that search.
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
        meets = title_quote_meets_minimum(candidate) if for_title else quote_meets_minimum(candidate)
        if not meets:
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

    def in_document(quote: Any, start: Any = None, end: Any = None, *, for_title: bool = False) -> dict[str, Any]:
        return accept_quote(quote, source, start, end, covered=covered, for_title=for_title)

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

    title_located = in_document(data.get("title_quote", ""), for_title=True)
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
    matches = _heading_matches(text)
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


def grounding_phrase(reason: str) -> str:
    """Short abstain label for a grounding_reason.

    The four vote-facing labels are "quote not found", "section mismatch",
    "comparison unavailable", and "models disagree". Comparison phrases are
    applied by the vote policy. This helper covers the quote check.
    """
    key = (reason or "").strip().lower()
    if key in {"quote_not_in_section", "section_unmatched", "section_ambiguous"}:
        return "section mismatch"
    if key == "quote_too_short":
        return "quote too short"
    return "quote not found"


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
        phrase = grounding_phrase(str(matched.get("grounding_reason") or ""))
        reasons.append(f"Section summary is ungrounded ({phrase}).")
    for flag in ungrounded_high_severity_flags(analysis):
        target = str(flag.get("section") or "")
        covers = flag_covers(target, section_id)
        if matched is not None:
            covers = covers or flag_covers(target, str(matched.get("section_id", "")))
        if covers:
            label = flag.get("type") or "flag"
            phrase = grounding_phrase(str(flag.get("grounding_reason") or ""))
            reasons.append(
                f"Ungrounded high-severity flag ({label}) refers to this section ({phrase}) and was withheld."
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
