"""
Central configuration. All values are env-overridable so the lifecycle can be
demoed with small numbers and short timers, then tightened toward the white
paper's real parameters in a deployment.
"""

from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _positive_int_env(name: str, default: int) -> int:
    """An int env value. Non-integers and non-positive values keep `default`."""
    value = _int_env(name, default)
    return value if value > 0 else default


def _provider_id(raw: str | None, default: str) -> str:
    """Normalize a provider id. A blank value keeps `default`."""
    name = (raw or "").strip().lower()
    return name or default


# Vendor for the primary role: bill analysis, the operator Avatar vote, and
# chamber axis tags. "anthropic" and "openai" (any OpenAI-compatible
# /chat/completions server) are implemented. AVATAR_MODEL and CHECKER_MODEL
# are Anthropic ids. The OpenAI-compatible provider does not read them.
LLM_PROVIDER = _provider_id(os.environ.get("LLM_PROVIDER"), "anthropic")

# Model for the primary role when that role uses Anthropic.
MODEL = os.environ.get("AVATAR_MODEL", "claude-sonnet-4-6")

# OpenAI-compatible provider. The key is required even for a local server;
# Ollama and vLLM accept any non-empty placeholder. Model ids are per role so
# a Claude id is never sent to this endpoint.
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL") or "").strip() or "https://api.openai.com/v1"
OPENAI_MODEL = (os.environ.get("OPENAI_MODEL") or "").strip()
OPENAI_CHECKER_MODEL = (os.environ.get("OPENAI_CHECKER_MODEL") or "").strip()
# A DeepSeek reasoning call on one bill ran 78–94s and can pass 2 minutes.
# 120s was cutting those off. 600s covers a slow reasoning pass.
OPENAI_TIMEOUT = _float_env("OPENAI_TIMEOUT", 600.0)

# Output budget for one model call. Reasoning models spend this on reasoning
# tokens before they emit JSON, so 8000 is not enough for them. Comparison
# has its own knob and does not fall back to a smaller hidden default.
# Every analysis call sends one of these as max_tokens. Neither path is unbounded.
LLM_MAX_TOKENS = _positive_int_env("LLM_MAX_TOKENS", 32000)
COMPARISON_MAX_TOKENS = _positive_int_env("COMPARISON_MAX_TOKENS", 32000)

# Per-bill input cap, about 15k tokens. A pasted omnibus bill can be hundreds
# of thousands of characters; sending all of it costs dollars and can miss
# the context window. Read at analysis time so a process can change it
# without a re-import. Non-integers and non-positive values keep the default.
DEFAULT_MAX_BILL_CHARS = 60_000


def max_bill_chars() -> int:
    """Character cap for one bill before it is sent for analysis."""
    return _positive_int_env("AVATAR_MAX_BILL_CHARS", DEFAULT_MAX_BILL_CHARS)


def oversize_mode() -> str:
    """What to do when a bill is over `max_bill_chars`.

    `truncate` (default) analyzes a marked excerpt of the beginning.
    `reject` refuses the bill. Anything else is treated as `truncate`.
    """
    raw = (os.environ.get("AVATAR_OVERSIZE_MODE") or "truncate").strip().lower()
    if raw in {"reject", "truncate"}:
        return raw
    return "truncate"

# Endorsement thresholds by bill scope (white paper decision 8).
# Real numbers for a US-sized population: ~50,000 ordinary, ~500,000 major,
# ~5,000,000 constitutional. Demo defaults are scaled down so the lifecycle is
# reachable with the simulated-endorsement control; override via env for realism.
ENDORSEMENT_THRESHOLDS: dict[str, int] = {
    "ordinary": _int_env("ENDORSE_THRESHOLD_ORDINARY", 25),
    "major": _int_env("ENDORSE_THRESHOLD_MAJOR", 250),
    "constitutional": _int_env("ENDORSE_THRESHOLD_CONSTITUTIONAL", 2500),
}

# Mandatory cooling-off between draft-freeze and the proxy vote (white paper
# decision 9: 2-4 weeks). Demo default short; set 0 to advance immediately.
BILL_COOLING_OFF_SECONDS = _int_env("BILL_COOLING_OFF_SECONDS", 7 * 24 * 3600)

# Window during which an endorsement can be revoked (white paper decision 13:
# 24-hour revocation). After this it is locked in.
ENDORSEMENT_REVOCATION_SECONDS = _int_env("ENDORSEMENT_REVOCATION_SECONDS", 24 * 3600)

# Fraction of decisive (non-abstain) votes that must be "yes" for a section to
# pass. A bill passes only if every section passes (white paper decision 11 —
# per-section voting, no Christmas-tree bills).
SECTION_PASSAGE_RATIO = _float_env("SECTION_PASSAGE_RATIO", 0.5)

# Proxy Chamber: size of the synthetic citizen population whose Avatars vote
# alongside the operator's. Cost is constant regardless of size — the population
# votes deterministically from a one-time LLM "axis tagging" of the bill.
POPULATION_SIZE = _int_env("POPULATION_SIZE", 200)

# Deadzone for the deterministic compass vote: |alignment| below this abstains.
CHAMBER_VOTE_TAU = _float_env("CHAMBER_VOTE_TAU", 0.12)

# Second Claude pass (white paper decisions 25, 28, as this prototype implements
# them today): another model re-checks the operator's vote and flags sections
# where the two disagree. The default vendor is Anthropic for both roles, so
# this is same-vendor redundancy, not an independent model family. The checker
# role also covers delegate drift and cross-bill conflict checks.
CHECKER_PROVIDER = _provider_id(os.environ.get("CHECKER_PROVIDER"), LLM_PROVIDER)
CHECKER_MODEL = os.environ.get("CHECKER_MODEL", "claude-haiku-4-5")
MULTI_MODEL_CHECK = os.environ.get("MULTI_MODEL_CHECK", "1") not in ("0", "false", "False", "")

# Independent-family comparison of the bill analysis. Unset means off, and
# analysis behaves as before. COMPARISON_MODEL is required when this is set;
# it is never taken from AVATAR_MODEL.
# COMPARISON_VOTE_POLICY=abstain (default, and the safe setting): a
# high-severity red-flag disagreement makes the Avatar abstain on that section.
# If the comparison is unavailable, abstain makes the Avatar abstain.
# COMPARISON_VOTE_POLICY=review is for demos only. It does not always keep the
# primary position. If the comparison is unavailable and that section has no
# high-severity primary flag, review keeps the primary yes or no and sets
# needs_human_review. That yes or no is excluded from the automatic yes/no
# tallies. If the comparison is unavailable and that section has a
# high-severity primary flag, review abstains. Excluding a flagged yes or no
# shrinks the denominator, so anyone who can make the comparison model
# unavailable (a broken key, an outage, a blocked network, a timeout) for
# chosen sections could shift which votes count and flip a 50% passage result.
# Neither mode lets one model silently win.
COMPARISON_PROVIDER = _provider_id(os.environ.get("COMPARISON_PROVIDER"), "")
COMPARISON_MODEL = (os.environ.get("COMPARISON_MODEL") or "").strip()
_COMPARISON_POLICY = (os.environ.get("COMPARISON_VOTE_POLICY") or "abstain").strip().lower()
COMPARISON_VOTE_POLICY = _COMPARISON_POLICY if _COMPARISON_POLICY in ("abstain", "review") else "abstain"

# Capture defense (white paper decision 30): flag when a delegate-followed vote is
# inconsistent with that delegate's OWN recorded positions — a sign the delegate has
# drifted, been captured, or been impersonated. Only runs for grounded delegates.
DELEGATE_DRIFT_CHECK = os.environ.get("DELEGATE_DRIFT_CHECK", "1") not in ("0", "false", "False", "")

# Emergency fast-track (Weimar Art. 48 caution — emergency powers must be HARD to
# invoke): a time-sensitive bill skips the normal cooling-off, but must clear a higher
# endorsement bar (base x multiplier) and is flagged for mandatory post-hoc review.
EMERGENCY_THRESHOLD_MULTIPLIER = _int_env("EMERGENCY_THRESHOLD_MULTIPLIER", 2)
EMERGENCY_COOLING_OFF_SECONDS = _int_env("EMERGENCY_COOLING_OFF_SECONDS", 3600)

# Cross-bill conflict: when a bill freezes, check it against already-passed bills and
# flag contradictions/repeals/duplications for the citizen.
CROSS_BILL_CONFLICT_CHECK = os.environ.get("CROSS_BILL_CONFLICT_CHECK", "1") not in ("0", "false", "False", "")
