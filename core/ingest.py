"""Stage 1 — Data ingestion, cleaning, and quality auditing.

Converts heterogeneous inputs (CSV, GeoJSON, Parquet, Shapefile) to a
unified Parquet intermediate.  A lightweight quality report is produced
after every ingest run and committed to docs/data_quality_report.md.

Uses DuckDB for SQL-style transformations and PyArrow for Parquet I/O.
GeoPandas is optional; when unavailable, geometry is stored as WKT strings.
"""
from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

try:
    import geopandas as gpd
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

from .models import DataQualityReport

logger = logging.getLogger(__name__)


# ── File readers ─────────────────────────────────────────────────────────────

def read_geojson(path: str | Path) -> pd.DataFrame:
    """Load a GeoJSON file into a flat DataFrame (geometry stored as WKT)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = []
    for feat in data.get("features", []):
        row = feat.get("properties", {}).copy()
        geom = feat.get("geometry")
        if geom:
            coords = geom.get("coordinates", [])
            if geom["type"] == "LineString" and coords:
                mid = coords[len(coords) // 2]
                row["lon"] = mid[0]
                row["lat"] = mid[1]
            elif geom["type"] == "Point" and coords:
                row["lon"] = coords[0]
                row["lat"] = coords[1]
            row["geometry_type"] = geom["type"]
        records.append(row)
    return pd.DataFrame(records)


def read_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_parquet(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def read_any(path: str | Path) -> pd.DataFrame:
    """Auto-detect file format and return a DataFrame."""
    p = Path(path)
    ext = p.suffix.lower()
    readers = {
        ".geojson": read_geojson,
        ".json": read_geojson,
        ".csv": read_csv,
        ".parquet": read_parquet,
    }
    reader = readers.get(ext)
    if reader is None:
        raise ValueError(f"Unsupported file format: {ext}")
    return reader(p)


# ── Column normalization ──────────────────────────────────────────────────────

COLUMN_ALIASES: dict[str, str] = {
    # posted speed
    "speed_limit": "posted_speed",
    "posted_limit": "posted_speed",
    "maxspeed": "posted_speed",
    "speed_kph": "posted_speed",
    # p85
    "p85": "p85_speed",
    "speed_85th": "p85_speed",
    "p85_kph": "p85_speed",
    # aadt / traffic intensity (ADB probe datasets use "traffic_intensity")
    "traffic_volume": "aadt",
    "volume": "aadt",
    "traffic_intensity": "aadt",
    "annual_average_daily_traffic": "aadt",
    "avg_daily_traffic": "aadt",
    # ptw share
    "motorcycle_share": "ptw_share",
    "two_wheeler_share": "ptw_share",
    # road type
    "highway": "road_class",
    "road_type": "road_class",
    "fclass": "road_class",
}

REQUIRED_COLUMNS = ["segment_id"]
NUMERIC_COLUMNS = ["posted_speed", "p85_speed", "aadt", "ptw_share",
                   "intersection_density", "length_m", "lat", "lon"]
BOOL_COLUMNS = ["is_divided", "has_footpath", "school_within_200m",
                "market_within_200m", "transit_stop_within_100m", "sign_conflict"]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename aliased columns and coerce types."""
    df = df.rename(columns={k: v for k, v in COLUMN_ALIASES.items() if k in df.columns})

    # Auto-generate segment_id if absent
    if "segment_id" not in df.columns:
        df["segment_id"] = [f"seg_{i:05d}" for i in range(len(df))]

    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in BOOL_COLUMNS:
        if col in df.columns:
            df[col] = df[col].fillna(False).astype(bool)
        else:
            df[col] = False

    # Clip obviously wrong speeds
    for speed_col in ["posted_speed", "p85_speed"]:
        if speed_col in df.columns:
            df[speed_col] = df[speed_col].where(
                df[speed_col].between(5, 200), other=None
            )

    return df


# ── Quality audit ─────────────────────────────────────────────────────────────

def audit_quality(df: pd.DataFrame) -> DataQualityReport:
    n = len(df)
    has_posted = df["posted_speed"].notna().sum() if "posted_speed" in df.columns else 0
    has_p85 = df["p85_speed"].notna().sum() if "p85_speed" in df.columns else 0
    has_aadt = df["aadt"].notna().sum() if "aadt" in df.columns else 0
    has_footpath = (df["has_footpath"].notna().sum()
                    if "has_footpath" in df.columns else 0)
    sign_conflicts = int(df["sign_conflict"].sum()) if "sign_conflict" in df.columns else 0
    probe_coverage = round(100 * has_p85 / max(n, 1), 1)

    warnings = []
    if probe_coverage < 50:
        warnings.append(f"Only {probe_coverage}% of segments have P85 probe data.")
    if has_posted / max(n, 1) < 0.7:
        warnings.append("Posted speed coverage below 70% — fallback chain will be used.")

    return DataQualityReport(
        total_segments=n,
        segments_with_posted_speed=int(has_posted),
        segments_with_p85=int(has_p85),
        segments_with_aadt=int(has_aadt),
        segments_with_footpath_data=int(has_footpath),
        probe_coverage_pct=probe_coverage,
        sign_conflict_count=sign_conflicts,
        low_confidence_count=0,    # set after scoring
        medium_confidence_count=0,
        high_confidence_count=0,
        warnings=warnings,
    )


# ── Low-level ingest (internal) ───────────────────────────────────────────────

def ingest(source_path: str | Path, output_dir: Optional[str | Path] = None) -> pd.DataFrame:
    """Load, normalize, and optionally persist a single file to Parquet.

    Internal helper — prefer load_adb_data() as the public entry point.
    """
    logger.info(f"Ingesting {source_path}")
    df = read_any(source_path)
    df = normalize_columns(df)

    report = audit_quality(df)
    for w in report.warnings:
        logger.warning(w)

    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out / "segments_clean.parquet", index=False)
        report_path = out / "data_quality_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report.model_dump(), f, indent=2)
        logger.info(f"Parquet + quality report written to {out}")

    return df


# ── Public entry point ────────────────────────────────────────────────────────

def _save_intermediate(df: pd.DataFrame, report, output_dir: Optional[str | Path]) -> None:
    """Write Parquet + quality report to output_dir/intermediate/."""
    if not output_dir:
        return
    import json as _json
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "segments_clean.parquet", index=False)
    with open(out / "data_quality_report.json", "w", encoding="utf-8") as f:
        _json.dump(report.model_dump(), f, indent=2)


def _load_adb_geo_auto(path: Path, city: str, country: str) -> pd.DataFrame:
    """Call load_adb_geo from data/adb_loader.py (handles geopandas fallback internally)."""
    import sys as _sys
    BASE = Path(__file__).parent.parent
    _sys.path.insert(0, str(BASE))
    from data.adb_loader import load_adb_geo
    return load_adb_geo(path, city=city, country=country)


def _city_country_from_name(stem: str) -> tuple[str, str]:
    """Infer city/country from ADB filename stem."""
    s = stem.lower()
    if "maharashtra" in s:
        return "Maharashtra", "IN"
    if "thailand" in s:
        return "Thailand", "TH"
    return stem, "XX"


def load_adb_data(
    source: Optional[str | Path] = None,
    *,
    mode: str = "sample",
    data_dir: Optional[str | Path] = None,
    output_dir: Optional[str | Path] = None,
) -> pd.DataFrame:
    """Single entry point for all data loading.  The rest of the pipeline
    depends exclusively on the DataFrame this function returns.

    Modes
    -----
    "sample"  Bundled Peshawar illustrative sample — no external files needed.
    "file"    Single file from `source`. Tries ADB-aware loader for spatial
              files (.geojson/.gpkg); falls back to generic reader for CSVs.
              Used by the browser upload endpoint.
    "adb"     Scan `data_dir` (default: data/adb/) for ALL recognised ADB
              spatial files. Loads each with city/country inferred from filename,
              then concatenates into one DataFrame.
    """
    BASE = Path(__file__).parent.parent

    if mode == "sample":
        sample_path = BASE / "data" / "sample" / "peshawar_sample.geojson"
        if not sample_path.exists():
            logger.info("Sample not found — generating...")
            import subprocess, sys
            gen = BASE / "data" / "sample" / "generate_sample.py"
            subprocess.run([sys.executable, str(gen)], check=True)
        return ingest(sample_path, output_dir=output_dir)

    elif mode == "file":
        if source is None:
            raise ValueError("load_adb_data(mode='file') requires a source path.")
        resolved = Path(source)
        # Spatial files: use the ADB-aware loader (handles geopandas fallback internally)
        if resolved.suffix.lower() in (".geojson", ".gpkg", ".json"):
            try:
                city, country = _city_country_from_name(resolved.stem)
                df = _load_adb_geo_auto(resolved, city=city, country=country)
                df = normalize_columns(df)
                report = audit_quality(df)
                for w in report.warnings:
                    logger.warning(w)
                _save_intermediate(df, report, output_dir)
                return df
            except Exception as exc:
                logger.warning(f"ADB spatial loader failed ({exc}); using generic reader.")
        return ingest(resolved, output_dir=output_dir)

    elif mode == "adb":
        search_dir = Path(data_dir) if data_dir else BASE / "data" / "adb"

        # Collect GeoJSON files. For each region, prefer the GeoJSON (full
        # geometry + lon/lat) over the GPKG (which needs geopandas for geometry).
        # The helmet SPI from the GPKG Boundaries layers is loaded via HELMET_SPI
        # constants in adb_loader.py — no extra GPKG read needed here.
        geojsons = sorted(search_dir.glob("ADB_Innovation_*.geojson"))
        if not geojsons:
            # Fall back to any GPKG (attributes only, no map coordinates)
            geojsons = sorted(search_dir.glob("*.gpkg"))
            # Filter out boundaries files
            geojsons = [p for p in geojsons
                        if not any(k in p.name.lower()
                                   for k in ("boundaries", "boundary", "helmet"))]

        if not geojsons:
            raise FileNotFoundError(
                f"No ADB spatial files found in {search_dir}. "
                "Place ADB_Innovation_*.geojson or *.gpkg files there and re-run."
            )

        dfs = []
        for cand in geojsons:
            city, country = _city_country_from_name(cand.stem)
            logger.info(f"ADB mode: loading {cand.name} ({city}/{country})")
            df_c = _load_adb_geo_auto(cand, city=city, country=country)
            dfs.append(df_c)

        df = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
        logger.info(f"ADB mode: {len(df)} total segments from {len(dfs)} file(s)")
        df = normalize_columns(df)
        report = audit_quality(df)
        for w in report.warnings:
            logger.warning(w)
        _save_intermediate(df, report, output_dir)
        return df

    else:
        raise ValueError(f"Unknown mode '{mode}'. Choose: sample | file | adb")
