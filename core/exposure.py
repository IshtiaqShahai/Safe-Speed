"""Stage 3 — VRU exposure index.

VRU_index combines pedestrian-generator proximity, footpath absence,
and PTW traffic share. It multiplies the Speed Safety Score so that
segments dense with unrepresented road users are weighted up, not down.
"""
from __future__ import annotations
from .models import SegmentFeatures


def compute_vru_index(segment: SegmentFeatures, cfg: dict) -> float:
    """Return VRU vulnerability index in range [0.5, 2.0].

    Component weights are additive on a base of 1.0:
      +0.30  school within 200 m
      +0.30  market within 200 m
      +0.20  transit stop within 100 m
      +0.30  footpath absent (pedestrians walk in carriageway)
      +0.20  elevated PTW share (≥ high_ptw_share threshold)
      +0.20  high PTW share (≥ ptw_share_threshold)
    Cap at 2.0; floor at 0.5.
    """
    vru_cfg = cfg["vru"]
    index = 1.0

    if segment.school_within_200m:
        index += 0.30
    if segment.market_within_200m:
        index += 0.30
    if segment.transit_stop_within_100m:
        index += 0.20
    if not segment.has_footpath:
        index += 0.30
    if segment.ptw_share >= vru_cfg["high_ptw_share"]:
        index += 0.20
    if segment.ptw_share >= vru_cfg["ptw_share_threshold"]:
        index += 0.20

    return round(min(2.0, max(0.5, index)), 3)


def normalize_aadt(aadt: float, cfg: dict) -> float:
    """Map AADT to [0.0, 1.0] using configured low/high bounds."""
    lo = cfg["exposure"]["aadt_low"]
    hi = cfg["exposure"]["aadt_high"]
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (aadt - lo) / (hi - lo)))
