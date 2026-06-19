"""Extract per-city statistics from scored_segments.geojson."""
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent.parent

print("Loading scored_segments.geojson ...")
with open(ROOT / "docs" / "scored_segments.geojson", encoding="utf-8") as f:
    data = json.load(f)

feats = data["features"]
print(f"Total features: {len(feats)}\n")

by_city = {}
for feat in feats:
    p = feat["properties"]
    city = p.get("city", "Unknown")
    if city not in by_city:
        by_city[city] = []
    by_city[city].append(p)

for city, segs in sorted(by_city.items()):
    n = len(segs)
    posted = sum(1 for s in segs if s.get("posted_speed") or s.get("s_posted"))
    p85    = sum(1 for s in segs if s.get("p85") and float(s["p85"]) > 0)
    diag   = Counter(str(s.get("diagnosis", "?")) for s in segs)
    unsafe  = diag.get("unsafe_limit", 0)
    noncred = diag.get("non_credible_limit", 0)
    safe    = diag.get("safe", 0)

    scores = sorted(float(s.get("score") or 0) for s in segs)
    idx90  = int(0.90 * len(scores))
    p90    = scores[idx90]
    hp     = sum(1 for v in scores if v >= p90)

    lives  = sum(float(s.get("lives_saved_per_year") or 0) for s in segs)

    print(f"{city}:  n={n}")
    print(f"  posted={posted} ({100*posted/n:.1f}%)   p85={p85} ({100*p85/n:.1f}%)")
    print(f"  unsafe_limit={unsafe}   non_credible={noncred}   safe={safe}")
    print(f"  90th-pct score threshold={p90:.2f}   high-priority (>= threshold)={hp}")
    print(f"  lives_saved_per_year (est.)={lives:.1f}")
    print()
