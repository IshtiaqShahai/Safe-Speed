"""Unit tests for Stage 3 — VRU exposure index."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import pytest
from core.models import SegmentFeatures
from core.exposure import compute_vru_index, normalize_aadt

with open(Path(__file__).parent.parent / "core" / "config.yaml") as f:
    CFG = yaml.safe_load(f)


def seg(**kwargs) -> SegmentFeatures:
    defaults = dict(
        segment_id="test",
        has_footpath=True,
        school_within_200m=False,
        market_within_200m=False,
        transit_stop_within_100m=False,
        ptw_share=0.0,
    )
    defaults.update(kwargs)
    return SegmentFeatures(**defaults)


class TestVRUIndex:
    def test_bare_segment_returns_base(self):
        s = seg()
        assert compute_vru_index(s, CFG) == pytest.approx(1.0)

    def test_school_adds_to_index(self):
        s_no  = seg()
        s_yes = seg(school_within_200m=True)
        assert compute_vru_index(s_yes, CFG) > compute_vru_index(s_no, CFG)

    def test_no_footpath_increases_index(self):
        s = seg(has_footpath=False)
        assert compute_vru_index(s, CFG) > 1.0

    def test_all_factors_raises_index(self):
        s = seg(
            school_within_200m=True,
            market_within_200m=True,
            transit_stop_within_100m=True,
            has_footpath=False,
            ptw_share=0.35,
        )
        idx = compute_vru_index(s, CFG)
        assert idx > 1.5

    def test_index_capped_at_2(self):
        s = seg(
            school_within_200m=True,
            market_within_200m=True,
            transit_stop_within_100m=True,
            has_footpath=False,
            ptw_share=0.45,
        )
        assert compute_vru_index(s, CFG) <= 2.0

    def test_index_floor_at_0_5(self):
        s = seg()
        assert compute_vru_index(s, CFG) >= 0.5


class TestNormalizeAADT:
    def test_low_aadt_maps_to_zero(self):
        val = normalize_aadt(CFG["exposure"]["aadt_low"], CFG)
        assert val == pytest.approx(0.0)

    def test_high_aadt_maps_to_one(self):
        val = normalize_aadt(CFG["exposure"]["aadt_high"], CFG)
        assert val == pytest.approx(1.0)

    def test_midpoint_roughly_half(self):
        mid = (CFG["exposure"]["aadt_low"] + CFG["exposure"]["aadt_high"]) / 2
        val = normalize_aadt(mid, CFG)
        assert val == pytest.approx(0.5, abs=0.01)

    def test_clamp_below_zero(self):
        assert normalize_aadt(-1000, CFG) == 0.0

    def test_clamp_above_one(self):
        assert normalize_aadt(1_000_000, CFG) == 1.0
