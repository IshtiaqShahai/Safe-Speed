"""Stage 4 — S_safe rules engine.

Every rule is traceable to a specific citation in config.yaml.
No speed value is produced by a model; all logic is explicit and unit-tested.
"""
from __future__ import annotations
from typing import Tuple, List
from .models import SegmentFeatures


def compute_s_safe(segment: SegmentFeatures, cfg: dict) -> Tuple[float, str, List[str]]:
    """Return (s_safe_km_h, primary_rule_code, list_of_fired_rule_descriptions).

    Rules are evaluated independently; the minimum (most restrictive) wins.
    This mirrors the Safe System hierarchy: protect the most vulnerable first.

    Rules (from config.yaml safe_speeds):
      VRU_MIXING     → 30 km/h  (WHO/GRSF; Stockholm Declaration §11)
      AT_GRADE_INT   → 50 km/h  (WHO/GRSF side-impact survivability)
      UNDIVIDED      → 70 km/h  (Austroads / iRAP head-on survivability)
      DIVIDED        → 80 km/h  (minimum; national limits apply)
    """
    ss = cfg["safe_speeds"]
    vru_cfg = cfg["vru"]
    fired: List[Tuple[float, str, str]] = []  # (speed, code, description)

    # ── Rule 1: VRU Mixing ────────────────────────────────────────────────
    has_generators = (
        segment.school_within_200m
        or segment.market_within_200m
        or segment.transit_stop_within_100m
    )
    no_segregation = not segment.has_footpath
    high_ptw = segment.ptw_share >= vru_cfg["ptw_share_threshold"]

    if has_generators and (no_segregation or high_ptw):
        reasons = []
        if segment.school_within_200m:
            reasons.append("school within 200 m")
        if segment.market_within_200m:
            reasons.append("market within 200 m")
        if segment.transit_stop_within_100m:
            reasons.append("transit stop within 100 m")
        if no_segregation:
            reasons.append("no footpath")
        if high_ptw:
            reasons.append(f"PTW share {segment.ptw_share:.0%}")
        fired.append((
            ss["vru_mixing"],
            "VRU_MIXING",
            "VRU mixing — " + "; ".join(reasons),
        ))

    # ── Rule 2: At-grade intersections ───────────────────────────────────
    if segment.intersection_density >= vru_cfg["intersection_density_threshold"]:
        fired.append((
            ss["intersection"],
            "AT_GRADE_INT",
            f"At-grade intersections — density {segment.intersection_density:.1f}/km"
            f" ≥ threshold {vru_cfg['intersection_density_threshold']:.1f}/km",
        ))

    # ── Rule 3 / 4: Carriageway geometry ─────────────────────────────────
    if segment.is_divided:
        fired.append((
            ss["divided"],
            "DIVIDED",
            "Divided carriageway — no opposing-traffic head-on risk",
        ))
    else:
        fired.append((
            ss["undivided"],
            "UNDIVIDED",
            "Undivided two-way carriageway — head-on collision risk",
        ))

    # Most restrictive rule wins
    fired.sort(key=lambda x: x[0])
    s_safe, code, desc = fired[0]
    all_descs = [d for _, _, d in fired]
    return float(s_safe), code, all_descs


def recommended_speed(s_safe: float) -> float:
    """Round S_safe down to the nearest standard speed-limit increment (10 km/h)."""
    return float(10 * int(s_safe / 10))


def intervention_for_diagnosis(diagnosis: str) -> str:
    """Map diagnosis to the least-cost intervention class that is effective."""
    mapping = {
        "unsafe_limit": "sign_plus_calming",
        "non_credible_limit": "redesign",
        "design_enabled_risk": "redesign",
        "safe": "sign_only",
        "insufficient_data": "sign_only",
    }
    return mapping.get(diagnosis, "sign_only")
