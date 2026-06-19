"""Verify pipeline output files are correct."""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent

with open(ROOT / "docs" / "network_summary.json") as f:
    s = json.load(f)
print("=== network_summary.json ===")
for k, v in s.items():
    print(f"  {k}: {v}")

with open(ROOT / "docs" / "data_quality_report.json") as f:
    q = json.load(f)
print("\n=== data_quality_report.json ===")
for k, v in q.items():
    print(f"  {k}: {v}")

# Count features quickly
with open(ROOT / "docs" / "scored_segments.geojson") as f:
    content = f.read()
count = content.count('"type": "Feature"')
print(f"\n=== scored_segments.geojson ===")
print(f"  Feature count (approx): {count}")
print(f"  File size: {(ROOT / 'docs' / 'scored_segments.geojson').stat().st_size / 1e6:.1f} MB")
