"""Unit tests for Stage 6 — misalignment matrix, score, confidence."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import pytest
from core.models import SegmentFeatures, Diagnosis, Confidence
from core.scoring import diagnose, compute_score, grade_confidence, score_segment

with open(Path(__file__).parent.parent / "core" / "config.yaml") as f:
    CFG = yaml.safe_load(f)


def seg(**kwargs) -> SegmentFeatures:
    defaults = dict(
        segment_id="test",
        is_divided=False,
        has_footpath=True,
        school_within_200m=False,
        market_within_200m=False,
        transit_stop_within_100m=False,
        intersection_density=0.0,
        ptw_share=0.0,
        probe_count=0,
    )
    defaults.update(kwargs)
    return SegmentFeatures(**defaults)


class TestDiagnosis:
    def test_unsafe_limit_when_posted_above_ssafe(self):
        d = diagnose(s_posted=60, s_safe=30, p85=58, cfg=CFG)
        assert d == Diagnosis.UNSAFE_LIMIT

    def test_non_credible_when_p85_far_above_posted(self):
        d = diagnose(s_posted=50, s_safe=70, p85=60, cfg=CFG)
        assert d == Diagnosis.NON_CREDIBLE_LIMIT

    def test_design_enabled_risk_when_p85_equals_posted_both_exceed_ssafe(self):
        d = diagnose(s_posted=40, s_safe=30, p85=41, cfg=CFG)
        assert d == Diagnosis.UNSAFE_LIMIT  # 40 > 30 → unsafe

    def test_safe_when_all_aligned(self):
        d = diagnose(s_posted=50, s_safe=70, p85=48, cfg=CFG)
        assert d == Diagnosis.SAFE

    def test_insufficient_data_when_no_posted(self):
        d = diagnose(s_posted=None, s_safe=50, p85=None, cfg=CFG)
        assert d == Diagnosis.INSUFFICIENT_DATA


class TestScoreFormula:
    def test_zero_score_when_no_gap(self):
        s = seg(posted_speed=50, p85_speed=48, aadt=5000)
        score, lg, bg, rr, exp = compute_score(s, s_safe=70, vru_index=1.0, cfg=CFG)
        assert lg == 0.0
        assert bg == 0.0
        assert score == 0.0

    def test_nonzero_score_when_posted_above_ssafe(self):
        s = seg(posted_speed=60, p85_speed=58, aadt=10000)
        score, lg, bg, rr, exp = compute_score(s, s_safe=30, vru_index=1.2, cfg=CFG)
        assert score > 0
        assert lg > 0

    def test_score_capped_at_100(self):
        s = seg(posted_speed=120, p85_speed=120, aadt=50000)
        score, _, _, _, _ = compute_score(s, s_safe=30, vru_index=2.0, cfg=CFG)
        assert score <= 100.0

    def test_score_increases_with_higher_aadt(self):
        s_low  = seg(posted_speed=60, p85_speed=60, aadt=500)
        s_high = seg(posted_speed=60, p85_speed=60, aadt=50000)
        score_low,  *_ = compute_score(s_low,  s_safe=30, vru_index=1.0, cfg=CFG)
        score_high, *_ = compute_score(s_high, s_safe=30, vru_index=1.0, cfg=CFG)
        assert score_high > score_low


class TestConfidence:
    def test_high_when_all_fields_and_many_probes(self):
        s = seg(
            posted_speed=50, p85_speed=48, aadt=10000,
            has_footpath=True, probe_count=100
        )
        assert grade_confidence(s, CFG) == Confidence.HIGH

    def test_low_when_few_probes(self):
        s = seg(posted_speed=50, probe_count=3)
        assert grade_confidence(s, CFG) == Confidence.LOW

    def test_medium_when_enough_probes_but_missing_aadt(self):
        s = seg(posted_speed=50, probe_count=15, aadt=None)
        assert grade_confidence(s, CFG) == Confidence.MEDIUM

    def test_sign_conflict_downgrades_high_to_medium(self):
        s = seg(
            posted_speed=50, p85_speed=48, aadt=10000,
            has_footpath=True, probe_count=100, sign_conflict=True
        )
        assert grade_confidence(s, CFG) == Confidence.MEDIUM


class TestScoreSegmentIntegration:
    def test_full_scoring_returns_result(self):
        s = seg(
            segment_id="int_test",
            is_divided=False, has_footpath=False,
            school_within_200m=True, market_within_200m=True,
            intersection_density=5.0,
            posted_speed=60, p85_speed=55, aadt=15000,
            ptw_share=0.35, probe_count=120,
        )
        result = score_segment(s, CFG)
        assert result.segment_id == "int_test"
        assert result.s_safe == 30
        assert result.diagnosis == Diagnosis.UNSAFE_LIMIT
        assert result.score > 0
        assert result.recommended_speed == 30
