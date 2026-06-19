"""Unit tests for Stage 4 — S_safe rules engine.

Every rule, threshold, and expected output is tested independently so that
any threshold change in config.yaml is immediately caught.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import pytest
from core.models import SegmentFeatures
from core.safe_speed import compute_s_safe, intervention_for_diagnosis

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
    )
    defaults.update(kwargs)
    return SegmentFeatures(**defaults)


class TestVRUMixingRule:
    def test_school_no_footpath_fires_30(self):
        s = seg(school_within_200m=True, has_footpath=False)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 30
        assert code == "VRU_MIXING"

    def test_market_high_ptw_fires_30(self):
        s = seg(market_within_200m=True, ptw_share=0.31)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 30
        assert code == "VRU_MIXING"

    def test_school_with_footpath_no_high_ptw_does_not_fire_vru(self):
        s = seg(school_within_200m=True, has_footpath=True, ptw_share=0.05)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed > 30

    def test_transit_no_footpath_fires_30(self):
        s = seg(transit_stop_within_100m=True, has_footpath=False)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 30


class TestIntersectionRule:
    def test_high_density_fires_50_on_divided_road(self):
        s = seg(is_divided=True, intersection_density=5.0)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 50
        assert code == "AT_GRADE_INT"

    def test_low_density_does_not_fire_intersection(self):
        s = seg(is_divided=True, intersection_density=2.0)
        speed, _, _ = compute_s_safe(s, CFG)
        assert speed == 80  # divided, no other rules

    def test_threshold_boundary(self):
        threshold = CFG["vru"]["intersection_density_threshold"]
        s_below = seg(is_divided=True, intersection_density=threshold - 0.1)
        s_above = seg(is_divided=True, intersection_density=threshold)
        speed_below, _, _ = compute_s_safe(s_below, CFG)
        speed_above, _, _ = compute_s_safe(s_above, CFG)
        assert speed_below == 80
        assert speed_above == 50


class TestCarriagewayRule:
    def test_undivided_gives_70(self):
        s = seg(is_divided=False)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 70
        assert code == "UNDIVIDED"

    def test_divided_gives_80(self):
        s = seg(is_divided=True)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 80
        assert code == "DIVIDED"


class TestMostRestrictiveWins:
    def test_vru_beats_intersection(self):
        s = seg(school_within_200m=True, has_footpath=False, intersection_density=6.0)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 30
        assert code == "VRU_MIXING"

    def test_intersection_beats_undivided(self):
        s = seg(is_divided=False, intersection_density=6.0)
        speed, code, _ = compute_s_safe(s, CFG)
        assert speed == 50
        assert code == "AT_GRADE_INT"


class TestInterventionMapping:
    def test_unsafe_limit_maps_to_calming(self):
        assert intervention_for_diagnosis("unsafe_limit") == "sign_plus_calming"

    def test_noncredible_maps_to_redesign(self):
        assert intervention_for_diagnosis("non_credible_limit") == "redesign"

    def test_safe_maps_to_sign_only(self):
        assert intervention_for_diagnosis("safe") == "sign_only"
