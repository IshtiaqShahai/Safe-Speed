"""Pipeline orchestrator — runs all stages end-to-end.

Usage:
    python -m core.pipeline --mode sample
    python -m core.pipeline --mode adb --data-dir data/adb/

Stages 1–6 (ingest → score → simulate) run with no external dependencies.
Stage 7 (AI policy briefs) is reference architecture in agents/; not executed here.
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

from .ingest import load_adb_data
from .segments import df_to_segments
from .scoring import score_segment
from .simulator import simulate_network
from .models import SegmentFeatures, ScoringResult, DataQualityReport

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = BASE_DIR / "docs"
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_pipeline(
    df,
    cfg: dict,
    output_dir: Optional[str | Path] = None,
    run_agents: bool = False,
) -> tuple[list[dict], DataQualityReport]:
    """Run Stages 2–6 (+ optional Stage 7) on an already-loaded DataFrame.

    Data loading is handled exclusively by load_adb_data() in core/ingest.py.
    This function never touches raw files — it receives a clean DataFrame and
    returns (scored_segments, quality_report).

    Args:
        df:          Clean DataFrame from load_adb_data().
        cfg:         Loaded config dict.
        output_dir:  If set, write results to GeoJSON + quality report.
        run_agents:  If True and ANTHROPIC_API_KEY is set, run the AI panel.
    """
    import pandas as pd
    from .ingest import audit_quality

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(output_dir) if output_dir else DOCS_DIR

    # Stage 1 quality report (df already loaded and normalised by load_adb_data)
    qr_path = out / "intermediate" / "data_quality_report.json"
    quality_report: DataQualityReport
    if qr_path.exists():
        with open(qr_path) as f:
            quality_report = DataQualityReport(**json.load(f))
    else:
        quality_report = audit_quality(df)

    # Stage 2 — Segments
    logger.info("Stage 2: Segment matching")
    segments = df_to_segments(df, cfg)

    # Stages 3–6 — Scoring
    logger.info("Stage 3-6: VRU exposure -> S_safe -> evidence -> score")
    scored_list: list[dict] = []
    for seg in segments:
        result: ScoringResult = score_segment(seg, cfg)
        seg_dict = {k: (v.value if hasattr(v, 'value') else v) for k, v in seg.model_dump().items()}
        score_dict = {k: (v.value if hasattr(v, 'value') else v) for k, v in result.model_dump().items()}
        merged = {**seg_dict, **score_dict}
        scored_list.append(merged)

    quality_report.low_confidence_count = sum(
        1 for s in scored_list if s.get("confidence") == "Low"
    )
    quality_report.medium_confidence_count = sum(
        1 for s in scored_list if s.get("confidence") == "Medium"
    )
    quality_report.high_confidence_count = sum(
        1 for s in scored_list if s.get("confidence") == "High"
    )

    # Lives-saved simulator
    logger.info("Stage 6b: Lives-saved simulator")
    sim_results = simulate_network(scored_list, cfg)
    sim_map = {r.segment_id: r.model_dump() for r in sim_results}
    for s in scored_list:
        sim = sim_map.get(s["segment_id"])
        if sim:
            s["fatalities_reduction_pct"] = sim["fatalities_reduction_pct"]
            s["lives_saved_per_year"] = sim["lives_saved_per_year"]

    # Stage 7 — AI policy briefs (architecture reference only; not executed in demo)
    # Full implementation in agents/panel.py. Activate by calling run_panel() directly.
    _ = run_agents  # suppress unused-arg warning

    # Stage 8 — Publish
    logger.info("Stage 8: Writing outputs")
    _write_outputs(scored_list, quality_report, out, cfg)

    logger.info(
        f"Pipeline complete. {len(scored_list)} segments scored. "
        f"Results in {out}"
    )
    return scored_list, quality_report


def _high_priority_threshold(scored: list[dict], cfg: dict) -> float:
    """Return the score cutoff for 'high priority' using network percentile + floor."""
    import numpy as np
    scores = [float(s.get("score") or 0) for s in scored]
    if not scores:
        return 100.0
    pct = cfg.get("scoring", {}).get("high_priority_percentile", 90)
    floor = cfg.get("scoring", {}).get("high_priority_floor", 10.0)
    return round(max(floor, float(np.percentile(scores, pct))), 2)


def _write_outputs(scored: list[dict], report: DataQualityReport, out: Path, cfg: dict = None) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # GeoJSON
    features = []
    for s in scored:
        props = {k: v for k, v in s.items() if k not in ("geometry_wkt",)}
        # Stringify enum values
        for k, v in props.items():
            if hasattr(v, "value"):
                props[k] = v.value
        geom = None
        if s.get("lon") and s.get("lat"):
            geom = {"type": "Point", "coordinates": [s["lon"], s["lat"]]}
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    geojson = {"type": "FeatureCollection", "features": features}
    with open(out / "scored_segments.geojson", "w", encoding="utf-8") as f:
        json.dump(geojson, f, indent=2, default=str)

    # Quality report
    with open(out / "data_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, indent=2)

    # Network summary
    diagnoses = {}
    for s in scored:
        d = str(s.get("diagnosis", "unknown"))
        diagnoses[d] = diagnoses.get(d, 0) + 1
    total = len(scored)
    unsafe_pct = round(100 * diagnoses.get("unsafe_limit", 0) / max(total, 1), 1)
    lives_total = sum(
        s.get("lives_saved_per_year") or 0 for s in scored
    )

    hp_threshold = _high_priority_threshold(scored, cfg or {})

    # Per-city breakdown at the SAME combined threshold (so they sum to total HP)
    per_city: dict[str, dict] = {}
    for s in scored:
        city = str(s.get("city") or s.get("country") or "unknown")
        if city not in per_city:
            per_city[city] = {"total": 0, "unsafe_limit": 0, "non_credible_limit": 0,
                              "high_priority": 0, "lives_saved_per_year": 0.0}
        c = per_city[city]
        c["total"] += 1
        d = str(s.get("diagnosis", ""))
        if d == "unsafe_limit":
            c["unsafe_limit"] += 1
        elif d == "non_credible_limit":
            c["non_credible_limit"] += 1
        if (s.get("score") or 0) >= hp_threshold:
            c["high_priority"] += 1
        c["lives_saved_per_year"] += (s.get("lives_saved_per_year") or 0)
    for c in per_city.values():
        c["lives_saved_per_year"] = round(c["lives_saved_per_year"], 2)

    summary = {
        "total_segments": total,
        "diagnosis_distribution": diagnoses,
        "unsafe_limit_pct": unsafe_pct,
        "estimated_lives_saved_per_year": round(lives_total, 2),
        "high_priority_threshold": hp_threshold,
        "high_priority_segments": sum(1 for s in scored if (s.get("score") or 0) >= hp_threshold),
        "per_city": per_city,
    }
    with open(out / "network_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


# ── CLI entry point ──────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="SafeSpeed pipeline")
    parser.add_argument("--mode", choices=["sample", "adb", "osm"], default="sample")
    parser.add_argument("--data-dir", default=None, help="Path to ADB data directory")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    cfg = load_config()

    try:
        df = load_adb_data(
            mode=args.mode,
            data_dir=args.data_dir,
            output_dir=Path(args.output or DOCS_DIR) / "intermediate",
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        sys.exit(1)

    run_pipeline(df, cfg, output_dir=args.output)


if __name__ == "__main__":
    main()
