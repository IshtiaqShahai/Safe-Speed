"""Stage 5 — Evidence extraction.

Resolves the posted speed and P85 for each segment from potentially
multiple sources, applying a deterministic fallback chain and flagging
conflicts between tabular data and Mapillary sign detections.
"""
from __future__ import annotations
from typing import Optional, Tuple
from .models import SegmentFeatures


FALLBACK_CHAIN = ["adb", "mapillary", "osm", "estimated"]


def resolve_posted_speed(
    adb_speed: Optional[float],
    mapillary_speed: Optional[float],
    osm_speed: Optional[float],
    road_class: str,
    cfg: dict,
) -> Tuple[Optional[float], str, bool]:
    """Return (posted_speed, source, sign_conflict).

    Fallback chain: ADB tabular → Mapillary detection → OSM maxspeed → estimated.
    A sign_conflict is flagged when ADB and Mapillary disagree by > 10 km/h.
    """
    sign_conflict = False

    if adb_speed is not None and mapillary_speed is not None:
        if abs(adb_speed - mapillary_speed) > 10:
            sign_conflict = True

    if adb_speed is not None:
        return adb_speed, "adb", sign_conflict

    if mapillary_speed is not None:
        return mapillary_speed, "mapillary", sign_conflict

    if osm_speed is not None:
        return osm_speed, "osm", False

    # Last resort: use road-class defaults from config
    defaults = cfg["osm"]["default_posted_speeds"]
    estimated = defaults.get(road_class, defaults["default"])
    return float(estimated), "estimated", False


def extract_p85(probe_speeds: list[float]) -> Tuple[Optional[float], int]:
    """Compute 85th-percentile speed from a list of probe observations.

    Returns (p85_kmh, sample_count).  Returns (None, 0) for empty input.
    """
    if not probe_speeds:
        return None, 0
    sorted_speeds = sorted(probe_speeds)
    n = len(sorted_speeds)
    idx = int(0.85 * n)
    idx = min(idx, n - 1)
    return round(sorted_speeds[idx], 1), n


def apply_evidence(segment: SegmentFeatures, cfg: dict) -> SegmentFeatures:
    """Apply evidence resolution in-place on a segment (for pipeline use).

    In demo / sample mode the segment already has posted_speed and p85_speed
    pre-populated; this function is a no-op in that case.
    """
    if segment.posted_speed is None:
        osm_defaults = cfg["osm"]["default_posted_speeds"]
        estimated = osm_defaults.get(segment.road_class, osm_defaults["default"])
        segment.posted_speed = float(estimated)
        segment.posted_speed_source = "estimated"

    return segment
