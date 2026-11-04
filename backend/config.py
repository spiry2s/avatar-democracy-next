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


# Model used for both the neutral analyzer and the Avatar voting engine.
# Opus-tier: red-flag detection and value-grounded voting are the quality-critical
# path, and Opus now costs ~1.7x Sonnet ($5/$25 vs $3/$15 per MTok), not the old
# 5x premium. Override with AVATAR_MODEL=claude-sonnet-4-6 for cheaper runs or
# claude-fable-5 for the premium tier (both also support adaptive thinking).
MODEL = os.environ.get("AVATAR_MODEL", "claude-opus-4-8")

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

# Capture defense (white paper decisions 25, 28): cross-check the operator's vote
# with a second, independent model and flag sections where the two disagree, so no
# single model silently decides. Cheap model keeps the overhead small.
CHECKER_MODEL = os.environ.get("CHECKER_MODEL", "claude-haiku-4-5")
MULTI_MODEL_CHECK = os.environ.get("MULTI_MODEL_CHECK", "1") not in ("0", "false", "False", "")

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
