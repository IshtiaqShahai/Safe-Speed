"""Run the Safe System Panel agent on top-N priority segments.

Requires: ANTHROPIC_API_KEY environment variable
Output:   docs/policy_briefs/<city>_<segment_id>.json

Usage:
    set ANTHROPIC_API_KEY=sk-ant-...
    python tools/run_panel.py --top 10
    python tools/run_panel.py --city Maharashtra --top 5
"""
import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def load_top_segments(geojson_path: Path, city: str | None, top_n: int) -> list[dict]:
    with open(geojson_path, encoding="utf-8") as f:
        data = json.load(f)
    segs = []
    for feat in data["features"]:
        p = feat.get("properties", {}) or {}
        if p.get("diagnosis") != "unsafe_limit":
            continue
        if city and p.get("city") != city:
            continue
        segs.append(p)
    segs.sort(key=lambda s: -(s.get("score") or 0))
    return segs[:top_n]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top",  type=int, default=5,   help="Number of segments")
    parser.add_argument("--city", type=str, default=None, help="Filter by city name")
    parser.add_argument("--geojson", default=str(ROOT / "docs" / "scored_segments.geojson"))
    parser.add_argument("--out",  default=str(ROOT / "docs" / "policy_briefs"))
    args = parser.parse_args()

    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("Set ANTHROPIC_API_KEY before running this script.")
        sys.exit(1)

    from core.pipeline import load_config
    from agents.panel import run_panel

    cfg = load_config()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = load_top_segments(Path(args.geojson), args.city, args.top)
    logger.info(f"Running panel on {len(segments)} segments ...")

    briefs = run_panel(segments, cfg)

    for brief in briefs:
        fname = f"{brief.segment_id.replace('/', '_')}.json"
        path = out_dir / fname
        with open(path, "w", encoding="utf-8") as f:
            json.dump(brief.model_dump(), f, indent=2, ensure_ascii=False)
        logger.info(f"Saved: {path}")

    logger.info(f"Done — {len(briefs)} briefs in {out_dir}")


if __name__ == "__main__":
    main()
