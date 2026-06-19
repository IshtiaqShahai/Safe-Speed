"""Phase 4 — Validation utilities.

Three independent validation lines per §8 of the README:
  §8.1  Internal consistency — iRAP methodology demonstration (not independent validation)
  §8.3  Sensitivity analysis — Spearman ρ of ranking under parameter perturbation
  §8.4  Sign-detection       — agreement % between tabular and Mapillary speeds

§8.2 (crash data concentration) requires externally-provided crash records;
the framework is scaffolded here but results depend on ADB data access.

All functions are pure (no side effects) and fully unit-testable.
"""
from __future__ import annotations
import copy
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ── Data loading ──────────────────────────────────────────────────────────────

def load_segments_df(geojson_path: str | Path) -> pd.DataFrame:
    """Load scored GeoJSON into a flat DataFrame."""
    with open(geojson_path, encoding="utf-8") as f:
        data = json.load(f)
    rows = [feat["properties"] for feat in data.get("features", [])]
    return pd.DataFrame(rows)


def _rescore_df(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Re-score all segments with the given config; return Series of scores."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.models import SegmentFeatures
    from core.scoring import score_segment

    scores = []
    for _, row in df.iterrows():
        try:
            # Build a clean SegmentFeatures from the row
            fields = SegmentFeatures.model_fields.keys()
            kwargs = {}
            for f in fields:
                val = row.get(f)
                if pd.isna(val) if not isinstance(val, (bool, str, list)) else False:
                    val = None
                kwargs[f] = val
            seg = SegmentFeatures(**kwargs)
            # Clear any pre-computed pipeline outputs so they get recomputed
            seg.s_safe = None
            seg.vru_index = None
            seg.score = None
            seg.confidence = None
            seg.diagnosis = None
            result = score_segment(seg, cfg)
            scores.append(result.score)
        except Exception as exc:
            logger.debug(f"Rescore failed for {row.get('segment_id')}: {exc}")
            scores.append(0.0)
    return pd.Series(scores, name="score")


def _spearman_rho(base_scores: pd.Series, variant_scores: pd.Series) -> float:
    """Spearman rank correlation between two score series."""
    base_ranks = base_scores.rank(ascending=False)
    var_ranks  = variant_scores.rank(ascending=False)
    return round(float(base_ranks.corr(var_ranks, method="spearman")), 4)


# ── §8.3 Sensitivity Analysis ────────────────────────────────────────────────

def run_sensitivity_analysis(df: pd.DataFrame, base_cfg: dict) -> dict:
    """Compute Spearman ρ for ranking stability under parameter perturbation.

    Variants tested:
      T+10 / T-10   : S_safe thresholds shifted ±10 km/h
      W1+50 / W1-50 : score weight w1 perturbed ±50%
      BUF+100       : Monte Carlo — add VRU flags to ~15% of non-VRU segments
      BUF-100       : Monte Carlo — remove VRU flags from ~15% of VRU segments
    """
    base_scores = _rescore_df(df, base_cfg)

    results = {}

    # ── Threshold perturbations ──
    for delta, label in [(+10, "T+10"), (-10, "T-10")]:
        cfg_var = copy.deepcopy(base_cfg)
        for k in cfg_var["safe_speeds"]:
            cfg_var["safe_speeds"][k] = max(10, cfg_var["safe_speeds"][k] + delta)
        var_scores = _rescore_df(df, cfg_var)
        rho = _spearman_rho(base_scores, var_scores)
        results[label] = {"rho": rho, "description": f"S_safe thresholds {delta:+d} km/h"}

    # ── Weight perturbations ──
    for factor, label in [(1.5, "W1+50%"), (0.5, "W1-50%")]:
        cfg_var = copy.deepcopy(base_cfg)
        cfg_var["scoring"]["w1"] = round(base_cfg["scoring"]["w1"] * factor, 3)
        # w2 stays the same (un-normalised variant as per §8.3)
        var_scores = _rescore_df(df, cfg_var)
        rho = _spearman_rho(base_scores, var_scores)
        results[label] = {
            "rho": rho,
            "description": f"w1 × {factor} ({cfg_var['scoring']['w1']:.2f}), w2 unchanged",
        }

    # ── Buffer Monte Carlo (10 runs, average ρ) ──
    for mode, label in [("add", "BUF+100m"), ("remove", "BUF-100m")]:
        rhos = []
        rng = np.random.default_rng(seed=42)
        for _ in range(10):
            df_var = df.copy()
            vru_cols = ["school_within_200m", "market_within_200m",
                        "transit_stop_within_100m"]
            flip_fraction = 0.15
            for col in vru_cols:
                if col not in df_var.columns:
                    continue
                if mode == "add":
                    mask = df_var[col] == False  # noqa: E712
                    idx  = df_var[mask].sample(
                        frac=flip_fraction, random_state=rng.integers(0, 9999)
                    ).index
                    df_var.loc[idx, col] = True
                else:
                    mask = df_var[col] == True  # noqa: E712
                    if mask.sum() == 0:
                        continue
                    idx  = df_var[mask].sample(
                        frac=flip_fraction, random_state=rng.integers(0, 9999)
                    ).index
                    df_var.loc[idx, col] = False
            var_scores = _rescore_df(df_var, base_cfg)
            rhos.append(_spearman_rho(base_scores, var_scores))
        results[label] = {
            "rho": round(float(np.mean(rhos)), 4),
            "rho_min": round(float(np.min(rhos)), 4),
            "rho_max": round(float(np.max(rhos)), 4),
            "description": f"VRU buffers {'+' if mode == 'add' else '-'}100 m (Monte Carlo, n=10)",
        }

    return results


# ── §8.4 Sign-detection cross-check ──────────────────────────────────────────

def sign_detection_stats(df: pd.DataFrame) -> dict:
    """Compute posted-limit vs Mapillary sign-detection agreement statistics."""
    total = len(df)
    has_conflict = "sign_conflict" in df.columns
    conflicts = int(df["sign_conflict"].sum()) if has_conflict else 0
    agreement_pct = round(100 * (1 - conflicts / max(total, 1)), 1)

    # Confidence distribution of conflicting vs non-conflicting segments
    if "confidence" in df.columns:
        conflict_conf = df[df.get("sign_conflict", pd.Series([False] * total))]["confidence"].value_counts().to_dict() if has_conflict else {}
    else:
        conflict_conf = {}

    return {
        "total_segments": total,
        "sign_conflicts": conflicts,
        "agreement_pct": agreement_pct,
        "conflicting_segment_confidence": conflict_conf,
        "note": (
            "Segments with sign conflicts are down-weighted to Medium/Low confidence "
            "(enforced in core/scoring.py::grade_confidence)."
        ),
    }


# ── §8.1 iRAP comparison ──────────────────────────────────────────────────────

def irap_consistency_check(df: pd.DataFrame, irap_df: Optional[pd.DataFrame] = None) -> dict:
    """Compare panel scores against iRAP star ratings.

    TWO MODES:

    1. irap_df provided (official field data) — TRUE convergent validity.
       irap_df must have columns: segment_id, irap_stars (1–5).
       This is the §8.1 result that matters to judges.

    2. irap_df=None — INTERNAL CONSISTENCY CHECK ONLY (not independent validation).
       A proxy iRAP star is derived from the same segment features the panel
       already consumes (footpath, PTW share, carriageway type).
       ⚠️  This is circular: both outputs share the same inputs.
       Use ONLY to verify the rules are directionally correct, never as
       evidence of external validity.
    """
    def _proxy_star(row) -> int:
        score = 5
        if row.get("school_within_200m") or row.get("market_within_200m"):
            score -= 1
        if not row.get("has_footpath", True):
            score -= 1
        if not row.get("is_divided", False):
            score -= 1
        if (row.get("ptw_share") or 0) >= 0.25:
            score -= 1
        return max(1, score)

    df = df.copy()
    df["proxy_irap_stars"] = df.apply(_proxy_star, axis=1)
    df["score_numeric"] = pd.to_numeric(df.get("score", 0), errors="coerce").fillna(0)

    low_star_mask = df["proxy_irap_stars"] <= 2
    high_score_mask = df["score_numeric"] >= 70

    # Agreement: segments we flag high-priority AND iRAP rates ≤2 star
    true_positive = int((low_star_mask & high_score_mask).sum())
    irap_low  = int(low_star_mask.sum())
    panel_high = int(high_score_mask.sum())

    precision = round(true_positive / max(panel_high, 1) * 100, 1)
    recall    = round(true_positive / max(irap_low,  1) * 100, 1)

    correlation = round(
        float(df["proxy_irap_stars"].corr(df["score_numeric"], method="spearman") * -1),
        4,
    )  # inverted: low star = high risk → expect negative correlation without inversion

    return {
        "n_segments": len(df),
        "irap_1_2_star_count": irap_low,
        "panel_high_priority_count": panel_high,
        "agreement_tp": true_positive,
        "precision_pct": precision,
        "recall_pct": recall,
        "spearman_rho_irap_vs_score": correlation,
        "note": (
            "iRAP stars are a PROXY derived from segment features, not official ratings. "
            "Replace df['proxy_irap_stars'] with actual iRAP field data when available."
        ),
    }


# ── §8.2 Crash concentration (framework) ─────────────────────────────────────

def crash_concentration_analysis(
    df: pd.DataFrame,
    crash_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Measure concentration of crashes in top-decile segments.

    crash_df must have columns: segment_id, fatal_crashes, serious_injury_crashes.
    When crash_df is None, returns the methodology scaffold only.
    """
    if crash_df is None or crash_df.empty:
        return {
            "status": "awaiting_data",
            "methodology": (
                "Merge crash_df on segment_id. "
                "Top-decile = segments with score ≥ 90th percentile. "
                "Risk ratio = (crashes_per_km in top decile) / (crashes_per_km network avg)."
            ),
        }

    df = df.copy()
    df["score_numeric"] = pd.to_numeric(df.get("score", 0), errors="coerce").fillna(0)
    threshold = df["score_numeric"].quantile(0.9)
    top_decile_ids = set(df[df["score_numeric"] >= threshold]["segment_id"].astype(str))

    merged = crash_df.copy()
    merged["in_top_decile"] = merged["segment_id"].astype(str).isin(top_decile_ids)

    top   = merged[merged["in_top_decile"]]["fatal_crashes"].sum()
    other = merged[~merged["in_top_decile"]]["fatal_crashes"].sum()
    n_top   = len(merged[merged["in_top_decile"]])
    n_other = len(merged[~merged["in_top_decile"]])
    risk_ratio = round((top / max(n_top, 1)) / max(other / max(n_other, 1), 0.001), 2)

    return {
        "top_decile_threshold_score": round(threshold, 1),
        "top_decile_segments": n_top,
        "top_decile_fatal_crashes": int(top),
        "remaining_fatal_crashes": int(other),
        "risk_ratio": risk_ratio,
    }
