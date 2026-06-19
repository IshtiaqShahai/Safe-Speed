"""Run Phase 4 validation analysis and print/save results.

Run from project root:
    python notebooks/run_sensitivity.py

Outputs:
    docs/sensitivity_results.json
    docs/validation_report.md  (written by companion script)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from core.validation import (
    load_segments_df,
    run_sensitivity_analysis,
    sign_detection_stats,
    irap_consistency_check,
)

GEOJSON = ROOT / "docs" / "scored_segments.geojson"
CONFIG  = ROOT / "core" / "config.yaml"
OUT_JSON = ROOT / "docs" / "sensitivity_results.json"

SEP = "-" * 60


def main():
    # ── Load ──────────────────────────────────────────────────────────────
    if not GEOJSON.exists():
        print("ERROR: docs/scored_segments.geojson not found. Run 'make demo' first.")
        sys.exit(1)

    with open(CONFIG) as f:
        cfg = yaml.safe_load(f)

    df = load_segments_df(GEOJSON)
    print(f"Loaded {len(df)} scored segments.\n")

    results = {}

    # ── §8.3 Sensitivity ─────────────────────────────────────────────────
    print(SEP)
    print("S8.3 Sensitivity Analysis -- Spearman rho of top-ranking stability")
    print(SEP)
    sens = run_sensitivity_analysis(df, cfg)
    results["sensitivity"] = sens

    print(f"{'Variant':<20} {'Spearman rho':>12}  Description")
    print("-" * 60)
    for name, row in sens.items():
        rho_str = f"{row['rho']:.4f}"
        extra = ""
        if "rho_min" in row:
            extra = f"  [min={row['rho_min']:.4f}, max={row['rho_max']:.4f}]"
        print(f"{name:<20} {rho_str:>12}  {row['description']}{extra}")

    # ── §8.4 Sign detection ──────────────────────────────────────────────
    print(f"\n{SEP}")
    print("S8.4 Sign-Detection Cross-Check")
    print(SEP)
    sign = sign_detection_stats(df)
    results["sign_detection"] = sign

    print(f"Total segments      : {sign['total_segments']}")
    print(f"Sign conflicts      : {sign['sign_conflicts']}")
    print(f"Agreement           : {sign['agreement_pct']}%")

    # ── §8.1 Internal consistency (iRAP methodology demo) ───────────────
    print(f"\n{SEP}")
    print("S8.1 Internal Consistency Check (iRAP Methodology Demo -- NOT independent validation)")
    print(SEP)
    irap = irap_consistency_check(df)
    results["irap_consistency"] = irap

    print(f"iRAP 1-2 star segments  : {irap['irap_1_2_star_count']}")
    print(f"Panel high-priority     : {irap['panel_high_priority_count']}")
    print(f"Agreement (TP)          : {irap['agreement_tp']}")
    print(f"Precision               : {irap['precision_pct']}%")
    print(f"Recall                  : {irap['recall_pct']}%")
    print(f"Spearman rho (iRAP/Score): {irap['spearman_rho_irap_vs_score']}")

    # ── Save ──────────────────────────────────────────────────────────────
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {OUT_JSON}")

    return results


if __name__ == "__main__":
    main()
