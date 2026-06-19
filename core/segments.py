"""Stage 2 — Segment matching and spatial layer join.

Spatially joins road network, probe data, Mapillary detections, and
context layers (schools, markets, stops) onto a single segment-level
GeoDataFrame — the single source of truth for all downstream stages.

GeoPandas + OSMnx are required for full spatial mode.
When unavailable the module falls back to attribute-only matching
(no distance-based VRU proximity, but all other logic remains valid).
"""
from __future__ import annotations
import logging
from typing import Optional
import pandas as pd

try:
    import geopandas as gpd
    from shapely.geometry import LineString, Point
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

from .models import SegmentFeatures
from .evidence import apply_evidence

logger = logging.getLogger(__name__)


# ── DataFrame → SegmentFeatures conversion ───────────────────────────────────

def row_to_segment(row: pd.Series) -> SegmentFeatures:
    """Convert a single DataFrame row to a SegmentFeatures model."""
    def get(field, default=None):
        val = row.get(field, default)
        if pd.isna(val) if not isinstance(val, (bool, str)) else False:
            return default
        return val

    return SegmentFeatures(
        segment_id=str(get("segment_id", f"seg_{row.name}")),
        geometry_wkt=get("geometry_wkt"),
        lat=get("lat"),
        lon=get("lon"),
        length_m=float(get("length_m", 500.0) or 500.0),
        road_class=str(get("road_class", "secondary") or "secondary"),
        is_divided=bool(get("is_divided", False)),
        has_footpath=bool(get("has_footpath", False)),
        intersection_density=float(get("intersection_density", 0.0) or 0.0),
        posted_speed=get("posted_speed"),
        p85_speed=get("p85_speed"),
        probe_count=int(get("probe_count", 0) or 0),
        aadt=get("aadt"),
        ptw_share=float(get("ptw_share", 0.0) or 0.0),
        school_within_200m=bool(get("school_within_200m", False)),
        market_within_200m=bool(get("market_within_200m", False)),
        transit_stop_within_100m=bool(get("transit_stop_within_100m", False)),
        posted_speed_source=str(get("posted_speed_source", "unknown") or "unknown"),
        sign_conflict=bool(get("sign_conflict", False)),
        country=str(get("country", "PK") or "PK"),
        city=str(get("city", "Peshawar") or "Peshawar"),
        urban=bool(get("urban", True)),
        road_name=get("road_name"),
    )


def df_to_segments(df: pd.DataFrame, cfg: dict) -> list[SegmentFeatures]:
    """Convert a clean DataFrame (from Stage 1) to a list of SegmentFeatures.

    Applies evidence resolution (Stage 5 fallback chain) for any segment
    missing a posted speed.
    """
    segments = []
    for _, row in df.iterrows():
        seg = row_to_segment(row)
        seg = apply_evidence(seg, cfg)
        segments.append(seg)
    logger.info(f"Converted {len(segments)} rows to SegmentFeatures")
    return segments


# ── OSM network download (Zero-Local-Data Mode) ──────────────────────────────

def download_osm_network(
    city: str,
    country: str,
    cfg: dict,
    cache_dir: Optional[str] = None,
) -> list[SegmentFeatures]:
    """Download road network from OpenStreetMap and build segment list.

    Requires osmnx.  Falls back gracefully with an error log if unavailable.
    """
    if not HAS_GEOPANDAS:
        logger.error("geopandas not installed — cannot use OSM network download.")
        return []
    try:
        import osmnx as ox
    except ImportError:
        logger.error("osmnx not installed — cannot download OSM network.")
        return []

    query = f"{city}, {country}"
    logger.info(f"Downloading OSM network for: {query}")
    G = ox.graph_from_place(query, network_type="drive")
    edges = ox.graph_to_gdfs(G, nodes=False)

    segments = []
    osm_defaults = cfg["osm"]["default_posted_speeds"]

    for i, (edge_id, row) in enumerate(edges.iterrows()):
        road_class = row.get("highway", "secondary")
        if isinstance(road_class, list):
            road_class = road_class[0]
        road_class = str(road_class)

        # Determine is_divided
        is_divided = road_class in cfg["osm"].get("road_class_divided", [])

        # Posted speed from OSM maxspeed tag
        maxspeed_raw = row.get("maxspeed")
        posted = None
        if maxspeed_raw and str(maxspeed_raw).replace(" km/h", "").strip().isdigit():
            posted = float(str(maxspeed_raw).replace(" km/h", "").strip())
        if posted is None:
            posted = float(osm_defaults.get(road_class, osm_defaults["default"]))

        geom = row.get("geometry")
        lat = lon = None
        if geom and hasattr(geom, "centroid"):
            c = geom.centroid
            lon, lat = c.x, c.y

        seg = SegmentFeatures(
            segment_id=f"osm_{i:06d}",
            lat=lat,
            lon=lon,
            length_m=float(row.get("length", 500.0) or 500.0),
            road_class=road_class,
            is_divided=is_divided,
            posted_speed=posted,
            posted_speed_source="osm",
            country=country,
        )
        segments.append(seg)

    logger.info(f"Downloaded {len(segments)} OSM segments for {query}")
    return segments
