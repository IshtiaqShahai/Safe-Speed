"""Dataset Explorer — run this on YOUR data file BEFORE the pipeline.

Usage:
    python tools/explore_dataset.py path/to/your_file.csv
    python tools/explore_dataset.py path/to/your_file.geojson
    python tools/explore_dataset.py data/adb/roads.csv

It will:
  1. Show all column names in your file
  2. Tell you which pipeline columns are FOUND, MISSING, or need RENAMING
  3. Show sample values for each column
  4. Give you the exact rename command to add to ingest.py if needed
  5. Show what the pipeline will do with missing columns (fallback)
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

# ── What the pipeline needs ───────────────────────────────────────────────────

PIPELINE_COLUMNS = {
    # name          : (required, description, fallback_if_missing)
    "segment_id"    : (False,  "Unique segment ID",                       "Auto-generated as seg_00001"),
    "posted_speed"  : (True,   "Posted speed limit (km/h)",               "Mapillary -> OSM default -> estimated"),
    "p85_speed"     : (False,  "85th percentile operating speed (km/h)",  "Skipped — diagnosis limited to limit-only"),
    "aadt"          : (False,  "Annual Average Daily Traffic",            "Default 5000 from config"),
    "road_class"    : (False,  "Road type (primary/secondary/trunk…)",    "Default: secondary"),
    "is_divided"    : (False,  "Divided carriageway? (True/False)",       "Default: False"),
    "has_footpath"  : (False,  "Footpath present? (True/False)",          "Default: False"),
    "intersection_density": (False, "Intersections per km",               "Default: 0.0"),
    "length_m"      : (False,  "Segment length in metres",               "Default: 500m"),
    "lat"           : (False,  "Latitude of segment midpoint",           "Optional — for map display"),
    "lon"           : (False,  "Longitude of segment midpoint",          "Optional — for map display"),
    "school_within_200m"  : (False, "School within 200m? (True/False)",  "Default: False"),
    "market_within_200m"  : (False, "Market within 200m? (True/False)",  "Default: False"),
    "transit_stop_within_100m": (False, "Transit stop nearby? (True/False)", "Default: False"),
    "ptw_share"     : (False,  "Powered two-wheeler fraction (0.0–1.0)", "Default: 0.0"),
    "probe_count"   : (False,  "Number of probe observations",           "Default: 0 (Low confidence)"),
    "road_name"     : (False,  "Street/road name",                       "Optional — for display only"),
    "urban"         : (False,  "Urban area? (True/False)",               "Default: True"),
    "city"          : (False,  "City name",                              "Default: Peshawar"),
    "country"       : (False,  "ISO country code (e.g. PK)",            "Default: PK"),
}

# Known aliases the pipeline already handles automatically
AUTO_ALIASES = {
    "speed_limit":    "posted_speed",
    "posted_limit":   "posted_speed",
    "maxspeed":       "posted_speed",
    "speed_kph":      "posted_speed",
    "p85":            "p85_speed",
    "speed_85th":     "p85_speed",
    "p85_kph":        "p85_speed",
    "traffic_volume": "aadt",
    "volume":         "aadt",
    "motorcycle_share": "ptw_share",
    "two_wheeler_share": "ptw_share",
    "highway":        "road_class",
    "road_type":      "road_class",
    "fclass":         "road_class",
}

SEP = "=" * 65


def load_file(path: Path) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext in (".csv",):
        return pd.read_csv(path)
    elif ext in (".geojson", ".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("type") == "FeatureCollection":
            rows = [f.get("properties", {}) for f in data.get("features", [])]
            return pd.DataFrame(rows)
        return pd.read_json(path)
    elif ext == ".parquet":
        return pd.read_parquet(path)
    elif ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    else:
        print(f"ERROR: Unsupported format '{ext}'. Supported: .csv .geojson .json .parquet .xlsx")
        sys.exit(1)


def analyze(df: pd.DataFrame, file_path: Path):
    your_cols = {c.lower(): c for c in df.columns}  # lowercase → original

    print(SEP)
    print(f"FILE    : {file_path.name}")
    print(f"ROWS    : {len(df):,}")
    print(f"COLUMNS : {len(df.columns)}")
    print(SEP)

    print("\nYOUR COLUMNS:")
    for col in df.columns:
        sample = df[col].dropna().head(3).tolist()
        print(f"  {col:<35} sample: {sample}")

    print(f"\n{SEP}")
    print("PIPELINE COLUMN MATCH ANALYSIS")
    print(SEP)

    found, auto_renamed, missing_required, missing_optional = [], [], [], []
    manual_renames_needed = []

    for pipe_col, (required, desc, fallback) in PIPELINE_COLUMNS.items():
        if pipe_col.lower() in your_cols:
            found.append(pipe_col)
        elif pipe_col.lower() in {a.lower(): t for a, t in AUTO_ALIASES.items() if t == pipe_col}:
            auto_renamed.append(pipe_col)
        else:
            # Check if any of your columns is an alias
            alias_match = None
            for your_col_lower, your_col_orig in your_cols.items():
                if AUTO_ALIASES.get(your_col_lower) == pipe_col:
                    alias_match = your_col_orig
                    break
            if alias_match:
                auto_renamed.append(f"{pipe_col}  (auto-renamed from '{alias_match}')")
            elif required:
                missing_required.append((pipe_col, fallback))
            else:
                missing_optional.append((pipe_col, fallback))

    print(f"\n  FOUND ({len(found)}):")
    for c in found:
        print(f"    + {c}")

    if auto_renamed:
        print(f"\n  AUTO-RENAMED by pipeline ({len(auto_renamed)}):")
        for c in auto_renamed:
            print(f"    ~ {c}")

    if missing_required:
        print(f"\n  MISSING REQUIRED ({len(missing_required)}):")
        for c, fb in missing_required:
            print(f"    ! {c:<30} -> fallback: {fb}")

    if missing_optional:
        print(f"\n  MISSING OPTIONAL ({len(missing_optional)}) — pipeline uses defaults:")
        for c, fb in missing_optional:
            print(f"    - {c:<30} -> {fb}")

    # ── Suggest manual renames ──────────────────────────────────────────────
    print(f"\n{SEP}")
    print("DO YOU NEED TO RENAME ANY COLUMNS?")
    print(SEP)

    unmatched_yours = []
    pipeline_targets = set(PIPELINE_COLUMNS.keys())
    all_handled = set(AUTO_ALIASES.values()) | set(PIPELINE_COLUMNS.keys())

    for your_col_lower, your_col_orig in your_cols.items():
        if your_col_orig not in all_handled and AUTO_ALIASES.get(your_col_lower) is None:
            if your_col_lower not in {p.lower() for p in PIPELINE_COLUMNS}:
                unmatched_yours.append(your_col_orig)

    if unmatched_yours:
        print(f"\n  Your unmatched columns: {unmatched_yours}")
        print()
        print("  If any of these contain pipeline data, add an alias to")
        print("  core/ingest.py in the COLUMN_ALIASES dict. Example:")
        print()
        print("    COLUMN_ALIASES = {")
        print('        ...')
        for col in unmatched_yours[:5]:
            print(f'        "{col}": "posted_speed",  # <- change target as needed')
        print("    }")
    else:
        print("  All your columns are already handled. No manual renames needed.")

    # ── Run recommendation ─────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("HOW TO LOAD THIS FILE INTO THE PIPELINE")
    print(SEP)

    rel_path = file_path.resolve()
    adb_path = ROOT / "data" / "adb" / file_path.name

    print(f"""
  Option A — ADB mode (recommended):
    1. Copy your file to:  data/adb/{file_path.name}
    2. Run:                make pipeline
       or:                python -m core.pipeline --mode adb

  Option B — Direct path:
    python -m core.pipeline --mode adb --data-dir {file_path.parent}

  Option C — Python API:
    from core.ingest import load_adb_data
    from core.pipeline import run_pipeline, load_config

    cfg = load_config()
    df = load_adb_data(source="{rel_path}", mode="file")
    scored, report = run_pipeline(df, cfg)
""")

    # ── Quick pipeline test ────────────────────────────────────────────────
    print(SEP)
    print("QUICK PIPELINE TEST (first 5 rows)")
    print(SEP)
    try:
        from core.ingest import normalize_columns
        from core.segments import df_to_segments
        from core.scoring import score_segment
        import yaml

        with open(ROOT / "core" / "config.yaml") as f:
            cfg = yaml.safe_load(f)

        df_norm = normalize_columns(df.head(5).copy())
        segs = df_to_segments(df_norm, cfg)
        print(f"\n  Scoring first {len(segs)} segments...")
        for seg in segs:
            result = score_segment(seg, cfg)
            name = seg.road_name or seg.segment_id
            print(f"  [{result.score:5.1f}] {result.diagnosis.value:<22} {name[:40]}")
        print("\n  Pipeline test PASSED. Your data loads correctly.")
    except Exception as e:
        print(f"\n  Pipeline test result: {e}")
        print("  (This is expected if column mapping is incomplete)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/explore_dataset.py <path_to_your_file>")
        print()
        print("Supported formats: .csv  .geojson  .json  .parquet  .xlsx")
        print()
        print("Example:")
        print("  python tools/explore_dataset.py data/adb/roads.csv")
        print("  python tools/explore_dataset.py C:/Downloads/adb_segments.geojson")
        sys.exit(0)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    df = load_file(path)
    analyze(df, path)


if __name__ == "__main__":
    main()
