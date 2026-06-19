"""ADB challenge data loader — maps the official ADB/Agilysis schema to the
pipeline's internal column names.

This is the ONLY module that knows about ADB-specific column names. Per the
single-loader principle, all downstream code (segments, scoring, simulator,
agents) depends exclusively on the normalised DataFrame this module returns.

Supported inputs
----------------
* GeoPackage (.gpkg)  — primary ADB delivery format (requires geopandas/fiona)
* GeoJSON (.geojson)  — works with or without geopandas (pure-Python fallback)

Data provenance (per "AI for Safer Roads 2026 — Data User Guide", Agilysis, May 2026):
  * Road network    : Overture Maps (OSM-derived), classes Motorway/Trunk/Primary/Secondary
  * Speed + traffic : TomTom Move probe samples at 10 km intervals
  * Land use        : NASA GRUMP urban/rural
  * SpeedLimit      : TomTom-derived, EXPLICITLY "Not validated" (User Guide §1.4)
  * AADT            : NOT available — only relative traffic percentiles exist

Key schema facts this loader encodes
------------------------------------
  * Speeds are in km/h (verified: limits 20-80 in MH, 20-120 in TH).
  * Only ~21-28% of segments have probe data (P85 > 0); ~21-25% have a posted limit.
  * Segments with AnalysisStatus != 'Valid' are flagged low-confidence, not dropped.
  * No footpath / school / market / PTW columns exist in ADB data. VRU flags
    default False. An optional urban-context heuristic is applied transparently.
  * Helmet-wearing SPI loaded from GPKG Boundaries layers / Excel; stored as
    metadata per region (does not alter speed scores).

Helmet-wearing SPI (source: ADB GPKG v02, Boundaries_4helmet / Province_Boundaries):
  Maharashtra — AllRidersSPI=0.56, DriverSPI=0.68, PassengerSPI=0.01
    Critical finding: near-zero pillion helmet compliance in this region.
  Thailand    — AllRidersSPI=0.778, DriverSPI=0.790, PassengerSPI=0.705
"""
from __future__ import annotations
import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Helmet SPI constants ──────────────────────────────────────────────────────
# Source: Maharashtra.gpkg → Boundaries_4helmet layer (4 rows, verified 2026-06-19):
#   OBJECTID 1  Mumbai          AllRiders=0.56  Driver=0.68  Passenger=0.01
#   OBJECTID 2  Pune            AllRiders=0.21  Driver=0.26  Passenger=0.01
#   OBJECTID 3  Maharashtra Rural AllRiders=0.15 Driver=0.20  Passenger=0.02
#   OBJECTID 4  Maharashtra Urban AllRiders=0.24 Driver=0.30  Passenger=0.01
# All segments tagged with the region-level values below (metadata only).
# PassengerSPI is consistently 0.01–0.02 across ALL sub-regions (the critical finding).
# AllRidersSPI stored here is the Mumbai row; the full range (0.15–0.56) is in README §3.
# Thailand source: Thailand.gpkg → Thailand_Province_Boundaries + Helmet Excel v02.
HELMET_SPI: dict[str, dict[str, float]] = {
    "Maharashtra": {
        "all_riders_spi": 0.56,   # Mumbai row (highest sub-region); range 0.15–0.56
        "driver_spi": 0.68,       # Mumbai row; range 0.20–0.68
        "passenger_spi": 0.01,    # CRITICAL: consistent 0.01–0.02 across all sub-regions
    },
    "Thailand": {
        "all_riders_spi": 0.778,
        "driver_spi": 0.790,
        "passenger_spi": 0.705,
    },
}

# ── ADB schema → pipeline schema mapping ─────────────────────────────────────
# Maharashtra and Thailand use slightly different column names; both covered.
ADB_COLUMN_MAP: dict[str, str] = {
    # posted speed (TomTom-derived, unvalidated)
    "SpeedLimit": "posted_speed",
    # operating speed
    "F85thPercentileSpeed": "p85_speed",
    "MedianSpeed": "median_speed",
    # road classification
    "RoadClass": "road_class",
    "class": "road_class_raw",          # fallback if RoadClass missing
    # probe sample size → confidence + exposure
    "Sample_Size_Total": "probe_count",
    "SampleSizeTotal":   "probe_count",   # Thailand spelling
    "SampleSize_avg":    "sample_size_avg",
    # traffic exposure proxies (NO true AADT exists — User Guide §1.4)
    "RankedPercentile":  "traffic_percentile",
    "WeightedSample":    "weighted_sample",
    # speed limit metadata
    "SpeedLimitFloor":   "speed_limit_floor",   # ADB analysis floor (rounded to 10)
    "ForAnalysis":       "speed_for_analysis",   # Thailand equivalent of SpeedLimitFloor
    # speeding behaviour
    "PercentOverLimit":  "percent_over_limit",
    "NumberOverLimit":   "number_over_limit",
    # context
    "LandUse":           "land_use",
    "UrbanPC":           "urban_pc",
    # identity / geometry helpers
    "DISSOLVE_ID":       "segment_id",
    "OvertureID":        "segment_id_alt",
    "OBJECTID":          "object_id",
    "names_primary":     "road_name",
    "english_ro":        "road_name_alt",
    "StreetImageLink":   "street_image_link",
    "AnalysisStatus":    "analysis_status",
    "ExcludeFromSpeedSPI": "exclude_from_spi",
    "Pass":              "adb_pass_flag",
}

# Road classes that are physically divided (no opposing-traffic head-on risk).
DIVIDED_CLASSES = {"motorway", "trunk"}


def _process_adb_df(
    df: pd.DataFrame,
    city: str,
    country: str,
    keep_invalid: bool = True,
) -> pd.DataFrame:
    """Apply ADB column mapping and derive pipeline fields. Pure Python — no geopandas."""
    # Rename ADB columns to pipeline names
    rename = {k: v for k, v in ADB_COLUMN_MAP.items() if k in df.columns}
    df = df.rename(columns=rename)

    # road_class: prefer RoadClass, else raw 'class'
    if "road_class" not in df.columns and "road_class_raw" in df.columns:
        df["road_class"] = df["road_class_raw"]
    if "road_class" in df.columns:
        df["road_class"] = df["road_class"].astype(str).str.lower()

    # segment_id: ensure string + unique
    if "segment_id" not in df.columns or df["segment_id"].isna().all():
        if "segment_id_alt" in df.columns:
            df["segment_id"] = df["segment_id_alt"]
        else:
            df["segment_id"] = [f"adb_{i:06d}" for i in range(len(df))]
    df["segment_id"] = df["segment_id"].astype(str)
    if df["segment_id"].duplicated().any():
        df["segment_id"] = (
            df["segment_id"] + "_" + df.groupby("segment_id").cumcount().astype(str)
        )

    # Numeric coercion
    for col in ["posted_speed", "p85_speed", "median_speed", "probe_count",
                "traffic_percentile", "weighted_sample", "percent_over_limit",
                "urban_pc", "length_m", "lat", "lon"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # P85 of 0 means "no probe data", not "speed is zero" → treat as missing
    if "p85_speed" in df.columns:
        df.loc[df["p85_speed"] <= 0, "p85_speed"] = None
    if "probe_count" in df.columns:
        df["probe_count"] = df["probe_count"].fillna(0).clip(lower=0).astype(int)

    # is_divided from road class
    road_cls = df.get("road_class", pd.Series(["secondary"] * len(df)))
    df["is_divided"] = road_cls.isin(DIVIDED_CLASSES)

    # urban flag (LandUse or UrbanPC)
    land_use = df.get("land_use", pd.Series([None] * len(df)))
    urban_pc = df.get("urban_pc", pd.Series([0.0] * len(df))).fillna(0)
    df["urban"] = (land_use.astype(str).str.upper() == "URBAN") | (urban_pc > 0.5)

    # AADT proxy from traffic percentile (NO true AADT — User Guide §1.4)
    # RankedPercentile is 0-100 (relative traffic rank). Mapped to a nominal
    # AADT range so the exposure term still differentiates high/low traffic roads.
    if "traffic_percentile" in df.columns:
        tp = df["traffic_percentile"].fillna(0).clip(0, 100) / 100.0
        df["aadt"] = 500 + tp * (50000 - 500)   # nominal 500..50000 range
        df["aadt_source"] = "traffic_percentile_proxy"
    else:
        df["aadt"] = None
        df["aadt_source"] = "none"

    # VRU columns: NOT in ADB data. Default False; do not invent.
    for col in ["school_within_200m", "market_within_200m",
                "transit_stop_within_100m", "has_footpath", "sign_conflict"]:
        df[col] = False
    df["ptw_share"] = 0.0

    # Transparent urban-context proxy for VRU mixing:
    # Urban + low posted-speed → flag market_within_200m so S_safe → 30 km/h.
    # This ADDS protection (lowers S_safe), never removes it.
    posted = df.get("posted_speed", pd.Series([99.0] * len(df))).fillna(99)
    df["urban_vru_proxy"] = df["urban"] & (posted <= 50)
    df.loc[df["urban_vru_proxy"], "market_within_200m"] = True
    df.loc[df["urban_vru_proxy"], "has_footpath"] = False

    # Metadata
    df["city"] = city
    df["country"] = country
    if "road_name" not in df.columns:
        df["road_name"] = df.get("road_name_alt", "Unknown")
    df["posted_speed_source"] = "adb_tomtom"

    # Helmet-wearing SPI — regional metadata (NOT a scoring input; policy brief context)
    # Source: ADB GPKG Boundaries_4helmet / Province_Boundaries layers
    helmet = HELMET_SPI.get(city, {})
    df["helmet_all_riders_spi"] = helmet.get("all_riders_spi")
    df["helmet_driver_spi"]     = helmet.get("driver_spi")
    df["helmet_passenger_spi"]  = helmet.get("passenger_spi")
    if helmet:
        pax = helmet.get("passenger_spi", 1.0)
        logger.info(
            f"Helmet SPI [{city}]: AllRiders={helmet.get('all_riders_spi')} "
            f"Driver={helmet.get('driver_spi')} Passenger={pax}"
            + (" ← CRITICAL: near-zero pillion compliance" if pax < 0.05 else "")
        )

    # Analysis-status flag
    if "analysis_status" in df.columns:
        invalid_mask = df["analysis_status"].astype(str).str.lower() != "valid"
        df["adb_valid"] = ~invalid_mask
        if not keep_invalid:
            n_before = len(df)
            df = df[df["adb_valid"]].copy()
            logger.info(f"Dropped {n_before - len(df)} non-Valid segments")
    else:
        df["adb_valid"] = True

    logger.info(
        f"ADB loader [{city}]: {len(df)} segments | "
        f"posted={int(df['posted_speed'].notna().sum())} | "
        f"p85={int(df['p85_speed'].notna().sum())} | "
        f"aadt_proxy={int((df['aadt'] > 0).sum())}"
    )
    return df


def load_adb_geojson_pure(
    path: str | Path,
    city: str = "Maharashtra",
    country: str = "IN",
    keep_invalid: bool = True,
) -> pd.DataFrame:
    """Pure-Python GeoJSON reader. No geopandas required.

    Reads GeoJSON features, extracts midpoint lon/lat for LineStrings, computes
    approximate length from coordinate deltas, then delegates to _process_adb_df().
    """
    path = Path(path)
    logger.info(f"Pure-Python GeoJSON reader: {path.name}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = []
    for feat in data.get("features", []):
        row = feat.get("properties", {}).copy()
        geom = feat.get("geometry")
        if geom:
            gtype = geom.get("type", "")
            coords = geom.get("coordinates", [])
            if gtype == "LineString" and coords:
                mid = coords[len(coords) // 2]
                row["lon"] = mid[0]
                row["lat"] = mid[1]
                # Haversine-approximate length along the polyline
                total_m = 0.0
                for i in range(len(coords) - 1):
                    dx = (coords[i + 1][0] - coords[i][0]) * 111320 * math.cos(
                        math.radians(coords[i][1])
                    )
                    dy = (coords[i + 1][1] - coords[i][1]) * 110540
                    total_m += math.sqrt(dx * dx + dy * dy)
                row["_geom_length_m"] = total_m
            elif gtype == "Point" and coords:
                row["lon"] = coords[0]
                row["lat"] = coords[1]
        records.append(row)

    df = pd.DataFrame(records)
    logger.info(f"Loaded {len(df)} features from {path.name}")

    # length_m: geometry estimate first, then ADB field fallbacks
    if "_geom_length_m" in df.columns:
        df["length_m"] = pd.to_numeric(df["_geom_length_m"], errors="coerce").fillna(500.0)
        df.drop(columns=["_geom_length_m"], inplace=True)
    elif "Shape_Length" in df.columns:
        # Shape_Length in ADB Maharashtra is in metres (CRS = UTM)
        df["length_m"] = pd.to_numeric(df["Shape_Length"], errors="coerce").fillna(500.0)
    elif "RoadLength" in df.columns:
        # RoadLength is in km → convert
        df["length_m"] = pd.to_numeric(df["RoadLength"], errors="coerce").fillna(0.5) * 1000.0
    else:
        df["length_m"] = 500.0

    return _process_adb_df(df, city=city, country=country, keep_invalid=keep_invalid)


def load_adb_geo(
    path: str | Path,
    layer: Optional[str] = None,
    city: str = "Maharashtra",
    country: str = "IN",
    keep_invalid: bool = True,
) -> pd.DataFrame:
    """Load an ADB GPKG or GeoJSON and return a pipeline-ready DataFrame.

    Tries geopandas for accurate UTM-projected lengths. Falls back to pure-Python
    reader automatically when geopandas/fiona are not installed (GeoJSON only).

    Parameters
    ----------
    keep_invalid : if True, AnalysisStatus != 'Valid' segments are kept but
        flagged (they receive Low confidence downstream). Default True.
    """
    path = Path(path)

    # Try geopandas path first
    try:
        import geopandas as gpd
        import fiona  # noqa: F401 (imported to validate availability)

        if path.suffix.lower() == ".gpkg":
            layers = fiona.listlayers(str(path))
            if layer is None:
                preferred = [l for l in layers
                             if any(k in l for k in ("Result", "Network", "ADB"))]
                layer = preferred[0] if preferred else layers[0]
            logger.info(f"Reading GPKG layer '{layer}' from {len(layers)} available")
            gdf = gpd.read_file(str(path), layer=layer)
        else:
            gdf = gpd.read_file(str(path))

        n_raw = len(gdf)
        logger.info(f"ADB geopandas loader: {n_raw} raw features from {path.name}")

        # lon/lat midpoint
        try:
            g = gdf.to_crs(4326) if gdf.crs and gdf.crs.to_epsg() != 4326 else gdf
            cent = g.geometry.representative_point()
            gdf = gdf.assign(lon=cent.x, lat=cent.y)
        except Exception as exc:
            logger.warning(f"Centroid computation failed: {exc}")
            gdf["lon"] = None
            gdf["lat"] = None

        # length_m from projected geometry
        try:
            proj = gdf.to_crs(gdf.estimate_utm_crs()) if gdf.crs else gdf
            gdf["length_m"] = proj.geometry.length
        except Exception:
            if "Shape_Length" in gdf.columns:
                gdf["length_m"] = pd.to_numeric(gdf["Shape_Length"], errors="coerce").fillna(500.0)
            else:
                gdf["length_m"] = 500.0

        df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
        return _process_adb_df(df, city=city, country=country, keep_invalid=keep_invalid)

    except ImportError:
        if path.suffix.lower() in (".geojson", ".json"):
            logger.warning("geopandas/fiona not available — using pure-Python GeoJSON fallback.")
            return load_adb_geojson_pure(path, city=city, country=country, keep_invalid=keep_invalid)
        if path.suffix.lower() == ".gpkg":
            logger.warning("geopandas/fiona not available — using sqlite3 GPKG fallback (no geometry).")
            return load_adb_gpkg_sqlite(path, layer=layer, city=city, country=country, keep_invalid=keep_invalid)
        raise ImportError(
            "geopandas and fiona are required to read GPKG files. "
            "Install with: pip install geopandas fiona"
        )


def load_adb_gpkg_sqlite(
    path: str | Path,
    layer: Optional[str] = None,
    city: str = "Maharashtra",
    country: str = "IN",
    keep_invalid: bool = True,
) -> pd.DataFrame:
    """Read ADB GPKG attributes via sqlite3 (no geopandas). Geometry is not parsed;
    lon/lat will be None. Scoring runs correctly — only map display is affected.

    The GPKG network layer names:
      Maharashtra : OvertureNetwork_wResults
      Thailand    : ADB_Results_D4
    """
    path = Path(path)
    logger.info(f"sqlite3 GPKG reader: {path.name}")

    con = sqlite3.connect(str(path))
    cur = con.cursor()

    # Find the network layer (feature table, not boundary)
    try:
        cur.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'")
        tables = [r[0] for r in cur.fetchall()]
    except Exception:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]

    # Pick the road-network layer (not boundaries)
    network_keywords = ("Network", "Result", "ADB_Results")
    boundary_keywords = ("Boundaries", "Province", "helmet")
    if layer is None:
        preferred = [t for t in tables
                     if any(k in t for k in network_keywords)
                     and not any(k.lower() in t.lower() for k in boundary_keywords)]
        layer = preferred[0] if preferred else tables[0]

    logger.info(f"Using GPKG layer: '{layer}' from {tables}")

    # Read all rows (geometry column contains binary blobs — skip it)
    cur.execute(f'PRAGMA table_info("{layer}")')
    col_info = [(r[1], r[2]) for r in cur.fetchall()]
    # Identify geometry column (BLOB type or named 'SHAPE'/'geom')
    geom_col_names = {c for c, t in col_info
                      if t.upper() in ("BLOB", "GEOMETRY") or c.upper() in ("SHAPE", "GEOM", "GEOMETRY")}
    non_geom = [c for c, _ in col_info if c not in geom_col_names]

    quoted = ", ".join('"' + c + '"' for c in non_geom)
    cur.execute(f'SELECT {quoted} FROM "{layer}"')
    rows = cur.fetchall()
    con.close()

    df = pd.DataFrame(rows, columns=non_geom)
    n = len(df)
    logger.info(f"sqlite3 GPKG: {n} rows from '{layer}' (no geometry)")

    # length_m from RoadLength (km) if available, else default
    if "RoadLength" in df.columns:
        df["length_m"] = pd.to_numeric(df["RoadLength"], errors="coerce").fillna(0.5) * 1000.0
    else:
        df["length_m"] = 500.0

    # lon/lat not available without geometry parsing
    df["lon"] = None
    df["lat"] = None

    return _process_adb_df(df, city=city, country=country, keep_invalid=keep_invalid)
