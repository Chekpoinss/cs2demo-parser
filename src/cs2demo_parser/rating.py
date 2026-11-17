"""Public regressions, NOT HLTV's unpublished Rating 2.0 implementation."""

import math


def approximate_impact(kpr: float, apr: float) -> float:
    """Approximate impact using kills and credited assists per round."""
    if not all(math.isfinite(value) and value >= 0 for value in (kpr, apr)):
        raise ValueError("KPR and APR must be finite, non-negative numbers")
    return 2.13 * kpr + 0.42 * apr - 0.41


def approximate_rating(*, kast: float, kpr: float, dpr: float, impact: float, adr: float) -> float:
    """The user-specified Rating 2.0 approximation. KAST is a percentage (0–100)."""
    values = (kast, kpr, dpr, impact, adr)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Rating inputs must be finite numbers")
    if not 0 <= kast <= 100:
        raise ValueError("KAST must be a percentage between 0 and 100")
    if min(kpr, dpr, adr) < 0:
        raise ValueError("KPR, DPR and ADR cannot be negative")
    return (
        0.0073 * kast
        + 0.3591 * kpr
        - 0.5329 * dpr
        + 0.2372 * impact
        + 0.0032 * adr
        + 0.1587
    )
