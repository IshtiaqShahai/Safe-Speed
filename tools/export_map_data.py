"""Export lightweight CSV for map visualisation from scored_segments.geojson.

Run once after pipeline:
    python tools/export_map_data.py
Output: docs/map_data.csv  (~8 MB vs 95 MB source)
"""
import json
import csv
from pathlib import Path

KEEP = [
    "segment_id", "lat", "lon", "score", "diagnosis",
    "city", "country", "posted_speed", "p85_speed", "s_safe", "s_safe_rule",
    "recommended_speed", "confidence", "lives_saved_per_year", "road_class",
    "helmet_passenger_spi", "length_m",
]

ROOT = Path(__file__).parent.parent
src  = ROOT / "docs" / "scored_segments.geojson"
dst  = ROOT / "docs" / "map_data.csv"

print(f"Reading {src} ...")
with open(src, encoding="utf-8") as f:
    data = json.load(f)

rows = []
skipped = 0
for feat in data["features"]:
    p = feat.get("properties", {}) or {}
    g = feat.get("geometry")
    if g and g.get("coordinates"):
        p["lon"] = g["coordinates"][0]
        p["lat"]  = g["coordinates"][1]
    if p.get("lat") is None or p.get("lon") is None:
        skipped += 1
        continue
    rows.append({k: p.get(k) for k in KEEP})

with open(dst, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=KEEP)
    writer.writeheader()
    writer.writerows(rows)

size_mb = dst.stat().st_size / 1e6
print(f"Exported {len(rows):,} segments to {dst}  ({size_mb:.1f} MB)")
if skipped:
    print(f"Skipped {skipped} segments with no coordinates")
