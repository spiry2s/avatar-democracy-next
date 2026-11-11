"""
A synthetic citizen population for the Proxy Chamber.

The real system is 200M Avatars; the demo seeds a configurable population of
synthetic citizens so aggregation is observable with one human operator. Each
synthetic citizen carries only a 6-axis compass (sampled from political
archetypes with noise). Their Avatars vote deterministically by aligning that
compass against the bill's per-section axis tags (see chamber.py) — no API call
per citizen, so the population scales for free.

Per-issue delegates for synthetic citizens are intentionally deferred to the
delegate-intelligence phase (following a delegate needs grounded positions, not
just a compass). Persisted to data/population.json and reused across votes.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

from backend import config, storage

log = logging.getLogger(__name__)
from backend.questionnaire import AXES

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
POPULATION_FILE = DATA_DIR / "population.json"

AXIS_NAMES = list(AXES.keys())  # economic, social, liberty, environment, foreign, governance

# Archetype compasses (axis -> lean in [-1, 1], + = the axis's "pos" pole).
# Used as cluster centers; each citizen is an archetype center + Gaussian noise.
ARCHETYPES: dict[str, dict[str, float]] = {
    "libertarian": {"economic": 0.7, "social": -0.2, "liberty": 0.85, "environment": 0.2, "foreign": -0.5, "governance": 0.6},
    "progressive": {"economic": -0.6, "social": -0.7, "liberty": 0.4, "environment": -0.8, "foreign": -0.2, "governance": -0.4},
    "conservative": {"economic": 0.5, "social": 0.7, "liberty": -0.3, "environment": 0.4, "foreign": 0.5, "governance": 0.3},
    "populist_left": {"economic": -0.7, "social": 0.1, "liberty": -0.1, "environment": -0.3, "foreign": -0.4, "governance": -0.2},
    "centrist": {"economic": 0.0, "social": 0.0, "liberty": 0.1, "environment": 0.0, "foreign": 0.0, "governance": 0.0},
}


def _clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _sample_citizen(idx: int, rng: random.Random) -> dict[str, Any]:
    archetype = rng.choice(list(ARCHETYPES.keys()))
    center = ARCHETYPES[archetype]
    compass = {axis: round(_clamp(center[axis] + rng.gauss(0, 0.25)), 3) for axis in AXIS_NAMES}
    return {"id": f"cit-{idx:05d}", "archetype": archetype, "compass": compass}


def generate_population(size: int | None = None, seed: int | None = None) -> list[dict[str, Any]]:
    """Generate and persist a fresh synthetic population."""
    size = size if size is not None else config.POPULATION_SIZE
    rng = random.Random(seed if seed is not None else time.time())
    citizens = [_sample_citizen(i, rng) for i in range(size)]
    save_population(citizens)
    return citizens


def load_population() -> list[dict[str, Any]]:
    """Load the population, generating a default one on first use.

    The population is synthetic, so an unreadable file is replaced with a new
    one. The old file is first moved aside (``population.json.corrupt-<time>``)
    and a warning is logged, so nothing disappears silently.
    """
    try:
        data = storage.read_json(POPULATION_FILE)
    except storage.CorruptStateError as exc:
        _set_aside(exc)
        return generate_population()
    if data is None:
        return generate_population()
    if not isinstance(data, list):
        _set_aside(storage.CorruptStateError(POPULATION_FILE, ValueError("not a JSON list")))
        return generate_population()
    return data


def _set_aside(exc: storage.CorruptStateError) -> None:
    target = POPULATION_FILE.with_name(f"{POPULATION_FILE.name}.corrupt-{int(time.time())}")
    try:
        POPULATION_FILE.replace(target)
        log.warning("population file unreadable (%s); moved to %s and regenerated", exc, target.name)
    except OSError:
        log.warning("population file unreadable (%s); regenerated", exc)


def save_population(citizens: list[dict[str, Any]]) -> None:
    storage.write_json(POPULATION_FILE, citizens)


def summary(citizens: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Population size + archetype breakdown, for the UI."""
    citizens = citizens if citizens is not None else load_population()
    counts: dict[str, int] = {}
    for c in citizens:
        counts[c.get("archetype", "?")] = counts.get(c.get("archetype", "?"), 0) + 1
    return {"size": len(citizens), "archetypes": counts}


def distribution(citizens: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Rich population breakdown for the Chamber dashboard: size, archetype counts,
    mean lean per axis, and each citizen's archetype + compass (for the scatter)."""
    citizens = citizens if citizens is not None else load_population()
    counts: dict[str, int] = {}
    sums: dict[str, float] = {a: 0.0 for a in AXIS_NAMES}
    for c in citizens:
        counts[c.get("archetype", "?")] = counts.get(c.get("archetype", "?"), 0) + 1
        comp = c.get("compass", {})
        for a in AXIS_NAMES:
            sums[a] += float(comp.get(a, 0.0))
    n = len(citizens) or 1
    return {
        "size": len(citizens),
        "archetypes": counts,
        "axis_means": {a: round(sums[a] / n, 3) for a in AXIS_NAMES},
        "axes": AXIS_NAMES,
        "citizens": [
            {"archetype": c.get("archetype", "?"), "compass": c.get("compass", {})}
            for c in citizens
        ],
    }
