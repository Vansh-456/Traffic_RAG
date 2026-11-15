"""Final scoring factors: freshness decay, source trust, spatial proximity.

final = rerank × freshness × trust × proximity
- freshness = exp(-Δt / τ); Δt = 0 while the chunk's validity window covers
  the reference time, else the gap to the window (or to published_at).
  τ: live 6 h, advisory/plan 3 days, general 7 days, historical disabled.
- trust: official 1.0, sensor 0.95, news 0.85
- proximity: 1 + α·(1 − d/radius) inside the radius (α = 0.3); outside it a
  location-mismatch decay max(0.2, exp(−(d − r)/2r)); 1 when geometry is unknown
"""

from __future__ import annotations

import math
from datetime import datetime

TAU_HOURS: dict[str, float | None] = {
    "live_conditions": 6,
    "cause_explanation": 6,
    "advisory_lookup": 72,
    "plan_trip": 72,
    "general_info": 168,
    "historical_lookup": None,
    "out_of_scope": None,
}
TRUST_WEIGHT = {"official": 1.0, "sensor": 0.95, "news": 0.85}
PROXIMITY_ALPHA = 0.3


def delta_hours(
    ref: datetime, valid_from: datetime | None, valid_to: datetime | None, published_at: datetime | None
) -> float:
    if valid_from or valid_to:
        start = valid_from or valid_to
        end = valid_to or valid_from
        assert start is not None and end is not None
        if start <= ref <= end:
            return 0.0
        gap = (start - ref) if ref < start else (ref - end)
        return abs(gap.total_seconds()) / 3600
    if published_at:
        return abs((ref - published_at).total_seconds()) / 3600
    return 24 * 30


def window_gap_hours(
    w_start: datetime, w_end: datetime, valid_from: datetime | None, valid_to: datetime | None
) -> float:
    """Hours between the query window and a validity window (0 when they overlap)."""
    vf = valid_from or valid_to
    vt = valid_to or valid_from
    assert vf is not None and vt is not None
    if vf <= w_end and vt >= w_start:
        return 0.0
    gap = (vf - w_end) if vf > w_end else (w_start - vt)
    return abs(gap.total_seconds()) / 3600


def freshness(intent: str, dt_hours: float, enabled: bool = True) -> float:
    tau = TAU_HOURS.get(intent, 168)
    if not enabled or tau is None:
        return 1.0
    return math.exp(-dt_hours / tau)


def trust(level: str) -> float:
    return TRUST_WEIGHT.get(level, 0.85)


PROXIMITY_FLOOR = 0.2


def proximity(distance_m: float | None, radius_m: float | None) -> float:
    """Boost inside the radius; decay outside it (location-mismatch penalty).

    Outside the radius: max(0.2, exp(-(d - r) / 2r)). Without this, a query
    about a place with no coverage (spatial filter dropped) would happily be
    answered with an equally worded advisory from the other side of the city.
    Chunks without geometry stay neutral (1.0).
    """
    if distance_m is None or not radius_m:
        return 1.0
    if distance_m < radius_m:
        return 1.0 + PROXIMITY_ALPHA * (1.0 - distance_m / radius_m)
    return max(PROXIMITY_FLOOR, math.exp(-(distance_m - radius_m) / (2 * radius_m)))
