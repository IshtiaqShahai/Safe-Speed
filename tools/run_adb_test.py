"""Run pipeline on real ADB data (data/adb/) and print diagnostic summary."""
import sys
import logging
import statistics
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.ingest import load_adb_data
from core.pipeline import run_pipeline, load_config

cfg = load_config()

print("\n" + "=" * 60)
print("STAGE 1: Loading ADB data (mode='adb')")
print("=" * 60)
df = load_adb_data(
    mode="adb",
    data_dir=ROOT / "data" / "adb",
    output_dir=ROOT / "docs" / "intermediate",
)
print(f"\nLoaded: {len(df)} rows | {len(df.columns)} columns")
print(f"\nKey field coverage:")
for col in ["segment_id", "posted_speed", "p85_speed", "aadt", "road_class",
            "is_divided", "urban", "probe_count", "length_m", "lat", "lon",
            "city", "adb_valid", "urban_vru_proxy"]:
    if col in df.columns:
        if df[col].dtype == bool or col in ("is_divided", "urban", "adb_valid", "urban_vru_proxy"):
            true_count = int(df[col].sum())
            print(f"  {col:<22}: {true_count:>7}/{len(df)} ({100*true_count/len(df):.1f}% True)")
        else:
            notnull = int(df[col].notna().sum())
            print(f"  {col:<22}: {notnull:>7}/{len(df)} ({100*notnull/len(df):.1f}% non-null)")
    else:
        print(f"  {col:<22}: MISSING")

if "city" in df.columns:
    print(f"\nCity breakdown: {dict(Counter(df['city']))}")

print("\n" + "=" * 60)
print("STAGES 2-8: Pipeline")
print("=" * 60)
scored, report = run_pipeline(df, cfg, output_dir=ROOT / "docs")

print(f"\nScored segments : {len(scored)}")
diag = Counter(s.get("diagnosis", "?") for s in scored)
conf = Counter(s.get("confidence", "?") for s in scored)
print(f"Diagnoses       : {dict(diag)}")
print(f"Confidence      : {dict(conf)}")

scores = [float(s.get("score") or 0) for s in scored]
nonzero = [v for v in scores if v > 0]
if nonzero:
    print(f"\nScore stats (all)   : min={min(scores):.1f}  "
          f"median={statistics.median(scores):.1f}  "
          f"max={max(scores):.1f}")
    print(f"Score stats (>0, n={len(nonzero)}): min={min(nonzero):.1f}  "
          f"median={statistics.median(nonzero):.1f}  "
          f"max={max(nonzero):.1f}")

# High priority using pipeline threshold
import json
summary_path = ROOT / "docs" / "network_summary.json"
if summary_path.exists():
    with open(summary_path) as f:
        summary = json.load(f)
    hp_thresh = summary.get("high_priority_threshold", 70)
    hp_count  = summary.get("high_priority_segments", 0)
    print(f"\nHigh-priority threshold (90th pct): {hp_thresh:.2f}")
    print(f"High-priority segments            : {hp_count}")
    print(f"Unsafe limit segments             : {diag.get('unsafe_limit', 0)}")
    print(f"Non-credible limit segments       : {diag.get('non_credible_limit', 0)}")
    print(f"Lives saved/year (est.)           : {summary.get('estimated_lives_saved_per_year', 0):.1f}")

print(f"\nQuality report warnings:")
for w in report.warnings:
    print(f"  WARN: {w}")

print(f"\nOutputs written to {ROOT / 'docs'}")
print("  scored_segments.geojson")
print("  network_summary.json")
print("  data_quality_report.json")
print("  intermediate/segments_clean.parquet")
