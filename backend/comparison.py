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

import contextvars
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from backend import config, llm, summarizer
from backend.grounding import flag_covers, flag_targets_all


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

# ł stays inside "zł". Splitting it would turn the currency into the word "z".
# This is for overlap and negations. The text around a number is compared
# separately and keeps every sign that is not in _HARMLESS_PUNCT.
_TOKEN = re.compile(r"[a-z0-9ł]+")
# Sentence punctuation, quotes, and the hyphens or dashes that sit between
# words. A dash in this set is ignored only between words. Every other
# sign, including § № # * ( ), stays and makes the two sides differ.
_HARMLESS_PUNCT = frozenset(
    ".,;:!?\"'"
    "\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f"
    "-\u2010\u2011\u2012\u2013\u2014\u2015"
)
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
    """NFKC, casefold, then expand contractions so "don't" and "cannot" expose "not".

    Folding happens before the number scan and the context comparison, so
    both sides of a match see the same characters. A curly apostrophe
    becomes the straight one that the apostrophe-thousands rule reads.
    """
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    folded = folded.replace("\u2019", "'").replace("\u2018", "'")
    folded = folded.replace("n't", " not ")
    return re.sub(r"\bcannot\b", " can not ", folded)


def _norm_phrase(text: str) -> str:
    return " ".join(_TOKEN.findall(_prepare(text)))


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(_prepare(text)))


def _negations(text: str) -> frozenset[str]:
    return frozenset(token for token in _tokens(text) if token in _NEGATIONS)


# Gaps that separate digit groups. A normal space, NBSP, narrow no-break
# space, thin space, figure space, tab, and line break are the same gap.
# They never glue the groups into one integer. Apostrophes still do:
# "1'500" is 1500. The word before the groups does not matter.
_GROUP_SPACES = frozenset(" \t\n\r\f\v\u00a0\u202f\u2009\u2007\u2028\u2029")
_APOSTROPHES = frozenset("'’ʼ")
_MONTHS = {
    # Polish genitive, the form used in "4 listopada 2026".
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5,
    "czerwca": 6, "lipca": 7, "sierpnia": 8, "września": 9, "wrzesnia": 9,
    "października": 10, "pazdziernika": 10, "listopada": 11, "grudnia": 12,
    # Norwegian.
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}
_VERSION_RE = re.compile(r"v(\d+(?:\.\d+)+)\b", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\b")
_DOT_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_NAMED_DATE_RE = re.compile(
    r"(\d{1,2})\.?\s+([a-ząćęłńóśźż]+)\s+(\d{4})\b",
    re.IGNORECASE,
)

# A mark is (kind, value, start, end). kind is number, date, version, or
# ambiguous. An ambiguous mark on either side fails the match: the check
# will not guess.
_Mark = tuple[str, str, int, int]


def _join_decimal(whole: str, frac: str) -> str:
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    if not frac:
        return whole
    return f"{whole}.{frac}"


def _valid_day_month(day: int, month: int) -> bool:
    return 1 <= day <= 31 and 1 <= month <= 12


def _date_value(year: int, month: int, day: int) -> str | None:
    if not _valid_day_month(day, month) or year < 1000:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _classify_number_body(body: str) -> tuple[str, str]:
    """Return ('number', canonical) or ('ambiguous', original).

    Mixed dot and comma: the separator that comes last is the decimal mark
    and the other is grouping. Grouping chunks must be exactly three digits.

    A comma after a leading 0 is a decimal mark, so 0,500 is 0.5 and
    0,500% is 0.5%. A single group of exactly three digits after a comma
    or a dot, with no other separator, is ambiguous: 1,500 and 1.500 can
    be 1500 or 1.5. The match fails. There is no bill-locale switch.

    These forms still resolve. 1,500,000 and 1.500.000 are thousands.
    1,500.00 and 1.500,00 use whichever separator comes last as the
    decimal mark, so both are 1500. A comma followed by one or two digits
    is a decimal comma, so 1,5 and 1,50 are 1.5.
    """
    if "," in body and "." in body:
        if body.rfind(",") > body.rfind("."):
            whole, frac = body.rsplit(",", 1)
            groups = whole.split(".")
        else:
            whole, frac = body.rsplit(".", 1)
            groups = whole.split(",")
        if not frac.isdigit() or not groups or not groups[0].isdigit():
            return ("ambiguous", body)
        if any(not part.isdigit() or len(part) != 3 for part in groups[1:]):
            return ("ambiguous", body)
        return ("number", _join_decimal("".join(groups), frac))
    if "," in body:
        parts = body.split(",")
        if (
            parts[0].isdigit()
            and set(parts[0]) <= {"0"}
            and len(parts) == 2
            and parts[1].isdigit()
        ):
            # 0,500 is five tenths, not five hundred. A comma after a
            # leading 0 is a decimal mark no matter how many digits follow.
            return ("number", _join_decimal("0", parts[1]))
        if all(part.isdigit() and len(part) == 3 for part in parts[1:]) and parts[0].isdigit():
            # One bare group (1,500 / 2,250) is ambiguous. Two or more
            # (1,500,000) can only be thousands.
            if len(parts) == 2:
                return ("ambiguous", body)
            return ("number", ("".join(parts)).lstrip("0") or "0")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) in (1, 2):
            return ("number", _join_decimal(parts[0], parts[1]))
        return ("ambiguous", body)
    if "." in body:
        parts = body.split(".")
        if not all(part.isdigit() for part in parts) or not parts[0]:
            return ("ambiguous", body)
        if len(parts) == 2 and len(parts[1]) == 3:
            return ("ambiguous", body)
        if len(parts) >= 3 and all(len(part) == 3 for part in parts[1:]):
            return ("number", ("".join(parts)).lstrip("0") or "0")
        if len(parts) == 2:
            return ("number", _join_decimal(parts[0], parts[1]))
        return ("ambiguous", body)
    if body.isdigit():
        return ("number", body.lstrip("0") or "0")
    return ("ambiguous", body)


def _match_version(text: str, i: int) -> _Mark | None:
    match = _VERSION_RE.match(text, i)
    if match is None:
        return None
    return ("version", match.group(1), match.start(), match.end())


def _match_date(text: str, i: int) -> _Mark | None:
    iso = _ISO_DATE_RE.match(text, i)
    if iso is not None:
        value = _date_value(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if value is None:
            return ("ambiguous", iso.group(0), iso.start(), iso.end())
        return ("date", value, iso.start(), iso.end())
    dotted = _DOT_DATE_RE.match(text, i)
    if dotted is not None:
        first, second, year = int(dotted.group(1)), int(dotted.group(2)), int(dotted.group(3))
        # Both parts fit a day and a month, so day.month and month.day are
        # both possible. That is ambiguous and must not match either reading.
        if first <= 12 and second <= 12:
            return ("ambiguous", dotted.group(0), dotted.start(), dotted.end())
        if 13 <= first <= 31 and second <= 12:
            value = _date_value(year, second, first)
        else:
            value = None
        if value is None:
            return ("ambiguous", dotted.group(0), dotted.start(), dotted.end())
        return ("date", value, dotted.start(), dotted.end())
    named = _NAMED_DATE_RE.match(text, i)
    if named is not None and named.group(2).lower() in _MONTHS:
        value = _date_value(int(named.group(3)), _MONTHS[named.group(2).lower()], int(named.group(1)))
        if value is None:
            return ("ambiguous", named.group(0), named.start(), named.end())
        return ("date", value, named.start(), named.end())
    return None


def _following_digit_groups(text: str, i: int) -> tuple[list[str], int] | None:
    """Raw digit groups after i, each one introduced by a grouping gap.

    The gap may be several characters. The digits are kept as written, so
    "012" is not "12" and "000" is not "0". None when no group follows.
    """
    groups: list[str] = []
    j = i
    while j < len(text) and text[j] in _GROUP_SPACES:
        k = j
        while k < len(text) and text[k] in _GROUP_SPACES:
            k += 1
        if k >= len(text) or not text[k].isdigit():
            break
        end = k + 1
        while end < len(text) and text[end].isdigit():
            end += 1
        groups.append(text[k:end])
        j = end
    if not groups:
        return None
    return groups, j


def _grouped_fraction(text: str, i: int) -> tuple[str, int] | None:
    """A decimal on the last space-separated group, or an ambiguous tail.

    One or two digits are a decimal comma or dot, so "500,50" is "500.5".
    Three or more digits can be thousands or a long fraction, so the mark
    fails closed. The integer digits of the group stay as written.
    """
    if i >= len(text) or text[i] not in ",.":
        return None
    end = i + 1
    while end < len(text) and text[end].isdigit():
        end += 1
    width = end - (i + 1)
    if width == 0:
        return None
    if width > 2:
        return ("ambiguous", end)
    frac = text[i + 1:end].rstrip("0")
    return (("." + frac) if frac else "", end)


def _match_number(text: str, i: int) -> _Mark | None:
    start = i
    prefix = ""
    if text[i] in "$€£":
        prefix = text[i]
        i += 1
        while i < len(text) and text[i] in _GROUP_SPACES:
            i += 1
    if i >= len(text) or not text[i].isdigit():
        return None
    body_start = i
    while i < len(text) and text[i].isdigit():
        i += 1
    first_end = i
    followed = _following_digit_groups(text, i)
    if followed is not None:
        # "12 500" stays the groups 12 and 500. It is not 12500, even when
        # a currency, a hyphen, or a citation word sits beside it.
        extra, i = followed
        groups = [text[body_start:first_end], *extra]
        fraction = _grouped_fraction(text, i)
        if fraction is not None:
            suffix, i = fraction
            if suffix == "ambiguous":
                if i < len(text) and text[i] == "%":
                    i += 1
                return ("ambiguous", " ".join(groups), start, i)
            groups[-1] = groups[-1] + suffix
        value = " ".join(groups)
        if prefix:
            value = prefix + value
        if i < len(text) and text[i] == "%":
            value += "%"
            i += 1
        return ("number", value, start, i)
    while i < len(text):
        ch = text[i]
        if ch in _GROUP_SPACES:
            break
        if ch in _APOSTROPHES:
            nxt = i + 1
            if nxt + 3 <= len(text) and text[nxt:nxt + 3].isdigit() and (
                nxt + 3 == len(text) or not text[nxt + 3].isdigit()
            ):
                i = nxt + 3
                continue
            break
        if ch == ",":
            nxt = i + 1
            if nxt < len(text) and text[nxt] in _GROUP_SPACES:
                grp = nxt + 1
                if grp + 3 <= len(text) and text[grp:grp + 3].isdigit() and (
                    grp + 3 == len(text) or not text[grp + 3].isdigit()
                ):
                    i = grp + 3
                    continue
                break
            end = nxt
            while end < len(text) and text[end].isdigit():
                end += 1
            width = end - nxt
            digits_so_far = "".join(ch for ch in text[body_start:i] if ch.isdigit())
            # 0,5000 is the same decimal as 0,500. The classifier already
            # treats a comma after a leading 0 as a decimal mark; the matcher
            # has to hand it that whole body, including four or more digits.
            if digits_so_far and set(digits_so_far) <= {"0"} and width >= 4:
                i = end
                break
            if width == 3:
                i = end
                continue
            if width in (1, 2):
                i = end
                break
            break
        if ch == ".":
            nxt = i + 1
            end = nxt
            while end < len(text) and text[end].isdigit():
                end += 1
            width = end - nxt
            if width == 0:
                break
            i = end
            if width == 3:
                continue
            break
        break
    percent = ""
    if i < len(text) and text[i] == "%":
        percent = "%"
        i += 1
    body = text[body_start:i - (1 if percent else 0)]
    # Drop grouping spaces and apostrophes before classification. They were
    # only consumed as groups of three digits.
    cleaned = "".join(ch for ch in body if ch not in _GROUP_SPACES and ch not in _APOSTROPHES)
    kind, value = _classify_number_body(cleaned)
    if kind == "number" and prefix:
        value = prefix + value
    if kind == "number" and percent:
        value += percent
    return (kind, value, start, i)


def _scan_marks(text: str) -> list[_Mark]:
    """Ordered quantities. Dates and versions are not plain numbers.

    Lists such as "sections 3, 4 and 5" stay three numbers in that order.
    A comma with a space is a list separator, not a decimal comma.
    """
    raw = _prepare(text)
    marks: list[_Mark] = []
    i = 0
    length = len(raw)
    while i < length:
        ch = raw[i]
        found: _Mark | None = None
        if ch == "v":
            found = _match_version(raw, i)
        elif ch.isdigit() or ch in "$€£":
            found = _match_date(raw, i) if ch.isdigit() else None
            if found is None:
                found = _match_number(raw, i)
        if found is None:
            i += 1
            continue
        marks.append(found)
        i = max(found[3], i + 1)
    return marks


def _has_ambiguous(marks: list[_Mark]) -> bool:
    return any(mark[0] == "ambiguous" for mark in marks)


def _structural(marks: list[_Mark]) -> tuple[tuple[str, str], ...]:
    return tuple((mark[0], mark[1]) for mark in marks)


def _is_dash(ch: str) -> bool:
    return ch in _HARMLESS_PUNCT and unicodedata.category(ch) == "Pd"


def _letter_beside(chars: list[str], index: int, step: int) -> bool:
    """True when a letter sits on this side, skipping spaces and dashes."""
    j = index + step
    while 0 <= j < len(chars):
        ch = chars[j]
        if ch.isspace() or _is_dash(ch):
            j += step
            continue
        return ch.isalpha()
    return False


def _context_kept(chars: list[str]) -> str:
    """Characters that still count once the quantity marks are blanked.

    Whitespace is dropped. Sentence punctuation and quotes are dropped.
    A hyphen or dash is dropped only when it sits between words. Everything
    else is kept, in order, so a repeated sign is not the same as one sign.
    """
    kept: list[str] = []
    for index, ch in enumerate(chars):
        if ch.isspace():
            continue
        if ch in _HARMLESS_PUNCT and not _is_dash(ch):
            continue
        if _is_dash(ch) and _letter_beside(chars, index, -1) and _letter_beside(chars, index, 1):
            continue
        kept.append(ch)
    return "".join(kept)


def _words_outside(text: str, marks: list[_Mark]) -> str:
    chars = list(_prepare(text))
    for _kind, _value, start, end in marks:
        for index in range(start, min(end, len(chars))):
            chars[index] = " "
    return _context_kept(chars)


def _numbers(text: str) -> tuple[str, ...]:
    """Resolved plain numbers, in order.

    An ambiguous amount becomes the single sentinel '<ambiguous>'. Dates and
    versions are omitted here; they are their own marks and still participate
    in the match.
    """
    marks = _scan_marks(text)
    if _has_ambiguous(marks):
        return ("<ambiguous>",)
    return tuple(mark[1] for mark in marks if mark[0] == "number")


def _polarity_agrees(left: str, right: str) -> bool:
    """False when negations differ, quantity sequences differ, or either side is ambiguous.

    Numbers, dates, and versions are ordered sequences, not sets. "from 5 to
    10" does not match "from 10 to 5". An unresolved amount such as "1.500"
    fails the match even when both sides wrote it the same way.
    """
    if _negations(left) != _negations(right):
        return False
    left_marks = _scan_marks(left)
    right_marks = _scan_marks(right)
    if _has_ambiguous(left_marks) or _has_ambiguous(right_marks):
        return False
    return _structural(left_marks) == _structural(right_marks)


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
    # Same words once amounts, dates, and versions are removed. This is what
    # lets "1,500" match "1500" without also treating a paraphrase as a match.
    if _words_outside(left, _scan_marks(left)) == _words_outside(right, _scan_marks(right)):
        return True
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
    # Quantity spans are blanked first, so "1,500" and "1500" share a word
    # set when the surrounding words are the same. A different word is still
    # a disagreement.
    return _words_outside(left, _scan_marks(left)) == _words_outside(right, _scan_marks(right))


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
        "status": "available",
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


_SECRET_TEXT = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{6,}|sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)",
    re.IGNORECASE,
)


def _public_failure(exc: BaseException) -> str:
    """A short reason safe to put in the API response. No key material."""
    text = f"{type(exc).__name__}: {exc}"
    text = _SECRET_TEXT.sub("[redacted]", text)
    text = " ".join(text.split())
    if len(text) > 400:
        text = text[:400] + "..."
    return text or type(exc).__name__


def _primary_high_on_section(analysis: dict[str, Any], section_id: str) -> bool:
    """True when the primary analysis has a high-severity flag on this section."""
    for flag in analysis.get("red_flags") or []:
        if not isinstance(flag, dict):
            continue
        if str(flag.get("severity") or "").strip().lower() != "high":
            continue
        target = str(flag.get("section") or "")
        if flag_targets_all(target) or flag_covers(target, section_id):
            return True
    return False


def _unavailable_note(policy: str, reason: str, prior: str, *, withhold_high: bool = False) -> str:
    why = "comparison unavailable"
    if reason:
        why += f" ({reason})"
    prior_label = prior or "abstain"
    if withhold_high:
        return (
            f"{why}. The primary model raised a high-severity flag on this section "
            f"and voted {prior_label}. That flag was not checked, so the Avatar abstained."
        )
    if policy == "review":
        return (
            f"{why}. The primary model voted {prior_label}. "
            "That yes or no is kept and flagged for human review. "
            "A failed comparison is not agreement, and it is not an unflagged yes."
        )
    return (
        f"{why}. The primary model voted {prior_label}; the Avatar abstained instead. "
        "A failed comparison is not a yes."
    )


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
            f"models disagree on a high-severity red flag ({detail}). "
            f"The primary model voted {prior_label}. That yes or no is kept and "
            "only flagged for human review. It is not withheld."
        )
    return (
        f"models disagree on a high-severity red flag ({detail}). "
        f"The primary model voted {prior_label}; the Avatar abstained instead, "
        "so neither model overrode the other."
    )


def apply_vote_policy(section_votes: list[Any], analysis: dict[str, Any]) -> set[str]:
    """Hold sections where the models disagree on a high-severity red flag.

    Returns the section ids whose position was forced to abstain. No-op when
    the analysis has no comparison report, so a vote with the checker role
    alone is unchanged.

    `review` is for demos only. Abstain is the safe setting. If the comparison
    is unavailable: (a) default `abstain` makes the Avatar abstain; (b) `review`
    with no high-severity primary flag on that section keeps the primary yes or
    no marked needs_human_review=true, which is excluded from the automatic
    yes/no tallies; (c) `review` with a high-severity primary flag on that
    section abstains. `review` does not always keep the primary position.
    On a high-severity disagreement, `abstain` replaces the position and
    `review` keeps the primary yes or no only as a needs_human_review flag,
    which is still excluded from the automatic yes/no tallies.
    """
    report = analysis.get("model_comparison") if isinstance(analysis, dict) else None
    if not isinstance(report, dict) or not report.get("enabled"):
        return set()
    policy = str(report.get("policy") or "abstain").strip().lower()
    if policy not in _POLICY_LABELS:
        policy = "abstain"
    if str(report.get("status") or "").strip().lower() == "unavailable":
        reason = str(report.get("unavailable_reason") or "")
        abstained: set[str] = set()
        for vote in section_votes:
            prior = str(getattr(vote, "position", "") or "")
            section_id = str(getattr(vote, "section_id", "") or "")
            withhold_high = _primary_high_on_section(analysis, section_id)
            vote.comparison_prior_position = prior
            vote.comparison_note = _unavailable_note(
                policy, reason, prior, withhold_high=withhold_high and policy == "review"
            )
            vote.confidence = "low"
            # review keeps a yes or no only when the primary raised no high-severity
            # flag on this section. A high flag that the comparison never checked
            # abstains, the same as the default policy.
            if policy == "review" and not withhold_high:
                vote.needs_human_review = True
                vote.comparison_abstained = False
            else:
                vote.position = "abstain"
                vote.comparison_abstained = True
                vote.needs_human_review = False
                abstained.add(section_id)
        return abstained
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


def _vendor(provider_id: str, model: str) -> str:
    """Who trains the model, not which API wrapper serves it.

    A model id may name the vendor (`anthropic/claude-haiku` on an
    OpenAI-compatible endpoint). Otherwise the vendor is the provider.
    """
    provider = str(provider_id or "").strip().lower()
    name = str(model or "").strip().lower()
    for separator in ("/", ":"):
        if separator in name:
            head = name.split(separator, 1)[0]
            if head:
                return head
    return provider


def model_relationship(
    primary_model: str,
    primary_provider: str,
    comparison_model: str,
    comparison_provider: str,
) -> str:
    """'same_vendor' or 'different'.

    The comparison is independent only when the vendors differ. Sonnet and
    Haiku are both Anthropic, so that pair is 'same_vendor' and is refused.
    """
    if _vendor(primary_provider, primary_model) == _vendor(comparison_provider, comparison_model):
        return "same_vendor"
    return "different"


def _empty_report_lists() -> dict[str, list[Any]]:
    return {
        "sections_only_primary": [],
        "sections_only_comparison": [],
        "sections_materially_different": [],
        "red_flags_only_primary": [],
        "red_flags_only_comparison": [],
        "high_severity_disagreements": [],
        "beneficiary_differences": [],
        "beneficiary_group_conflicts": [],
    }


def _unavailable_report(
    *,
    reason: str,
    policy: str,
    primary_info: dict[str, str],
    comparison_info: dict[str, str],
) -> dict[str, Any]:
    """A failed comparison. This is not agreement and must not be read as one."""
    policy = policy if policy in _POLICY_LABELS else "abstain"
    report = {
        "enabled": True,
        "status": "unavailable",
        "unavailable_reason": reason,
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
            "comparison unavailable. The comparison model call failed, so there "
            "is no second analysis to agree with. This is not agreement. "
            "The Avatar abstains on every section, or flags each one for human "
            "review when COMPARISON_VOTE_POLICY=review. A failed check is not a yes."
        ),
    }
    report.update(_empty_report_lists())
    return report


def _merge_usage(analysis: dict[str, Any], extra: list[dict[str, Any]]) -> None:
    current = list(analysis.get("token_usage") or [])
    current.extend(extra)
    analysis["token_usage"] = current


def analyze_for_api(bill_text: str) -> dict[str, Any]:
    """Primary analysis, plus a comparison report when that provider is configured.

    Both passes call `summarizer.analyze_bill`, so they share the prompt, the
    truncation, and the JSON parse. They run concurrently. A missing comparison
    credential or a same-vendor pair still fails before either call. A failure
    of the comparison call itself keeps the primary analysis and marks the
    comparison unavailable. That is not agreement.
    """
    comparison_provider = None
    if comparison_configured():
        # Fail before either call when the comparison role is misconfigured.
        comparison_provider = llm.get_provider("comparison")
        primary_id = (config.LLM_PROVIDER or "anthropic").strip().lower() or "anthropic"
        primary_model = llm.model_for(primary_id, "primary")
        relation = model_relationship(
            primary_model,
            primary_id,
            comparison_provider.model,
            comparison_provider.provider_id,
        )
        if relation == "same_vendor":
            raise llm.LLMConfigError(
                "The comparison only counts as independent when the models come "
                "from different vendors. "
                f"Both are {_vendor(primary_id, primary_model)!r}: "
                f"{primary_model} and {comparison_provider.model}. "
                "Sonnet and Haiku are the same vendor. "
                "This is not an independent check."
            )
    if comparison_provider is None:
        return summarizer.analyze_bill(bill_text).to_dict()

    policy = config.COMPARISON_VOTE_POLICY
    if policy not in _POLICY_LABELS:
        policy = "abstain"
    primary_provider = (config.LLM_PROVIDER or "anthropic").strip().lower() or "anthropic"
    primary_info = {
        "provider": primary_provider,
        "model": llm.model_for(primary_provider, "primary"),
    }
    comparison_info = {
        "provider": comparison_provider.provider_id,
        "model": comparison_provider.model,
    }

    def _run_primary() -> summarizer.BillAnalysis:
        return summarizer.analyze_bill(bill_text)

    def _run_comparison() -> summarizer.BillAnalysis:
        return summarizer.analyze_bill(
            bill_text,
            provider=comparison_provider,
            max_tokens=config.COMPARISON_MAX_TOKENS,
            usage_role="comparison",
        )

    primary_error: BaseException | None = None
    comparison_error: BaseException | None = None
    primary_result: summarizer.BillAnalysis | None = None
    other_result: summarizer.BillAnalysis | None = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        # Each worker runs in a copy of this request's context, so its model
        # calls reach the request's usage bucket even when the pass fails.
        # Invite budgets are settled from that bucket.
        primary_future = pool.submit(contextvars.copy_context().run, _run_primary)
        comparison_future = pool.submit(contextvars.copy_context().run, _run_comparison)
        try:
            primary_result = primary_future.result()
        except Exception as exc:
            primary_error = exc
        try:
            other_result = comparison_future.result()
        except Exception as exc:
            comparison_error = exc
    if primary_error is not None:
        raise primary_error
    assert primary_result is not None
    primary = primary_result.to_dict()
    if comparison_error is not None:
        failed_usage = llm.consume_usage(comparison_provider, role="comparison")
        _merge_usage(primary, failed_usage)
        primary["model_comparison"] = _unavailable_report(
            reason=_public_failure(comparison_error),
            policy=policy,
            primary_info=primary_info,
            comparison_info=comparison_info,
        )
        return primary
    assert other_result is not None
    _merge_usage(primary, list(other_result.token_usage or []))
    report = build_report(
        primary,
        other_result.to_dict(),
        primary_info=primary_info,
        comparison_info=comparison_info,
        policy=policy,
    )
    primary["model_comparison"] = report
    return primary
