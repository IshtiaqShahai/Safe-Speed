"""Quick schema inspection of ADB GeoJSON files."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

for fname in ["ADB_Innovation_Maharashtra.geojson", "ADB_Innovation_Thailand.geojson"]:
    fpath = ROOT / "data" / "adb" / fname
    if not fpath.exists():
        print(f"NOT FOUND: {fname}")
        continue

    print(f"\n{'='*60}")
    print(f"FILE: {fname}  ({fpath.stat().st_size / 1e6:.1f} MB)")
    print('='*60)

    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    feats = data.get("features", [])
    print(f"Total features: {len(feats)}")
    if not feats:
        continue

    geom = feats[0].get("geometry", {})
    print(f"Geometry type : {geom.get('type')}")

    props = feats[0].get("properties", {})
    print(f"\nColumns ({len(props)}):")
    for k, v in props.items():
        print(f"  {k:<35} = {repr(v)[:50]}")

    # Check a few more features for value ranges
    import statistics
    speed_vals = [f["properties"].get("SpeedLimit") for f in feats[:500] if f["properties"].get("SpeedLimit")]
    p85_vals   = [f["properties"].get("F85thPercentileSpeed") for f in feats[:500] if f["properties"].get("F85thPercentileSpeed") and f["properties"].get("F85thPercentileSpeed") > 0]
    if speed_vals:
        print(f"\nSpeedLimit sample (n={len(speed_vals)}): min={min(speed_vals)}, max={max(speed_vals)}, median={statistics.median(speed_vals)}")
    if p85_vals:
        print(f"F85th sample   (n={len(p85_vals)}): min={min(p85_vals)}, max={max(p85_vals)}, median={statistics.median(p85_vals)}")
