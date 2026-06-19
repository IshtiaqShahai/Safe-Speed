"""Stage 6 — Misalignment matrix, Speed Safety Score, and confidence grading.

This is the diagnostic heart of the pipeline.  No language model touches
any value produced here.
"""
from __future__ import annotations
from typing import Tuple
from .models import (
    SegmentFeatures, ScoringResult,
    Diagnosis, Confidence, InterventionClass,
)
from .safe_speed import compute_s_safe, intervention_for_diagnosis
from .exposure import compute_vru_index, normalize_aadt


# ── Misalignment Matrix ──────────────────────────────────────────────────────

def diagnose(
    s_posted: Optional[float],
    s_safe: float,
    p85: Optional[float],
    cfg: dict,
) -> Diagnosis:
    """Classify segment into one of four diagnosis categories.

    Ref: §2.3 Misalignment Matrix in README.
    """
    from typing import Optional  # local import to satisfy type checker
    if s_posted is None:
        return Diagnosis.INSUFFICIENT_DATA

    cred_ratio = cfg["credibility"]["p85_over_posted_ratio"]
    align_tol = cfg["credibility"]["design_alignment_tolerance"]
    safe_ratio = cfg["credibility"]["p85_over_safe_ratio"]

    # Condition 1: limit itself permits unsurvivable speeds
    if s_posted > s_safe:
        return Diagnosis.UNSAFE_LIMIT

    # Condition 2: limit credible but road design invites faster speeds
    if p85 is not None and p85 > cred_ratio * s_posted and s_posted <= s_safe:
        return Diagnosis.NON_CREDIBLE_LIMIT

    # Condition 3: limit and behavior both agree, both exceed safe envelope
    if p85 is not None:
        limit_in_alignment = abs(p85 - s_posted) / max(s_posted, 1) < align_tol
        if p85 > safe_ratio * s_safe and limit_in_alignment:
            return Diagnosis.DESIGN_ENABLED_RISK

    return Diagnosis.SAFE


# ── Speed Safety Score ───────────────────────────────────────────────────────

def compute_score(
    segment: SegmentFeatures,
    s_safe: float,
    vru_index: float,
    cfg: dict,
) -> Tuple[float, float, float, float, float]:
    """Return (score, limit_gap, behavior_gap, raw_risk, exposure).

    Formula (§2.4):
      LimitGap    = max(0, S_posted − S_safe) / S_safe
      BehaviorGap = max(0, P85 − max(S_posted, S_safe)) / S_safe
      RawRisk     = w1·LimitGap + w2·BehaviorGap
      Exposure    = norm(AADT) × VRU_index   [clamped 0.5–1.5]
      Score       = min(100, 100 × RawRisk × Exposure)
    """
    if segment.posted_speed is None:
        return 0.0, 0.0, 0.0, 0.0, cfg["scoring"]["exposure_min"]

    w1 = cfg["scoring"]["w1"]
    w2 = cfg["scoring"]["w2"]
    s_posted = segment.posted_speed
    p85 = segment.p85_speed

    limit_gap = max(0.0, s_posted - s_safe) / max(s_safe, 1.0)

    if p85 is not None:
        behavior_gap = max(0.0, p85 - max(s_posted, s_safe)) / max(s_safe, 1.0)
    else:
        behavior_gap = 0.0

    raw_risk = w1 * limit_gap + w2 * behavior_gap

    # AADT fallback chain:
    #   1. segment.aadt (field name or auto-aliased from traffic_intensity)
    #   2. probe_count as relative-volume proxy (scaled to aadt_high)
    #   3. default_aadt from config (mid-range conservative estimate)
    if segment.aadt:
        aadt = segment.aadt
    elif segment.probe_count and segment.probe_count > 0:
        # probe_count gives relative volume; scale to AADT range as proxy
        hi = cfg["exposure"]["aadt_high"]
        aadt = min(float(segment.probe_count) * cfg["exposure"].get("probe_to_aadt_scale", 20.0), hi)
    else:
        aadt = cfg["exposure"]["default_aadt"]
    aadt_norm = normalize_aadt(aadt, cfg)
    e_min = cfg["scoring"]["exposure_min"]
    e_max = cfg["scoring"]["exposure_max"]
    exposure = e_min + aadt_norm * (e_max - e_min) * vru_index
    exposure = max(e_min, min(e_max, exposure))

    score = min(100.0, 100.0 * raw_risk * exposure)
    return round(score, 2), round(limit_gap, 4), round(behavior_gap, 4), round(raw_risk, 4), round(exposure, 4)


# ── Confidence Grading ───────────────────────────────────────────────────────

def grade_confidence(segment: SegmentFeatures, cfg: dict) -> Confidence:
    """Assign High / Medium / Low confidence based on data completeness."""
    ccfg = cfg["confidence"]

    # Minimum probe threshold
    if segment.probe_count < ccfg["medium_min_probes"]:
        return Confidence.LOW

    # Required fields for Medium
    for field in ccfg["required_fields_medium"]:
        if getattr(segment, field, None) is None:
            return Confidence.LOW

    if segment.probe_count < ccfg["high_min_probes"]:
        return Confidence.MEDIUM

    # Required fields for High
    for field in ccfg["required_fields_high"]:
        val = getattr(segment, field, None)
        if val is None:
            return Confidence.MEDIUM

    # Sign conflict downgrades from High → Medium
    if segment.sign_conflict:
        return Confidence.MEDIUM

    return Confidence.HIGH


# ── Master scoring function ──────────────────────────────────────────────────

def score_segment(segment: SegmentFeatures, cfg: dict) -> ScoringResult:
    """Run all of Stage 6 on a single segment and return a ScoringResult."""
    s_safe, rule, _ = compute_s_safe(segment, cfg)
    vru_index = compute_vru_index(segment, cfg)
    score, limit_gap, behavior_gap, raw_risk, exposure = compute_score(
        segment, s_safe, vru_index, cfg
    )
    diagnosis = diagnose(segment.posted_speed, s_safe, segment.p85_speed, cfg)
    confidence = grade_confidence(segment, cfg)
    rec_speed = float(10 * int(s_safe / 10)) if s_safe >= 10 else s_safe
    interv = intervention_for_diagnosis(diagnosis.value if hasattr(diagnosis, 'value') else str(diagnosis))

    return ScoringResult(
        segment_id=segment.segment_id,
        s_safe=s_safe,
        s_safe_rule=rule,
        s_posted=segment.posted_speed,
        p85=segment.p85_speed,
        vru_index=vru_index,
        limit_gap=limit_gap,
        behavior_gap=behavior_gap,
        raw_risk=raw_risk,
        exposure=exposure,
        score=score,
        confidence=confidence,
        diagnosis=diagnosis,
        recommended_speed=rec_speed,
        intervention_class=InterventionClass(interv),
    )
