"""Unit tests for the Nilsson–Elvik lives-saved simulator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
import pytest
from core.models import SimulatorInput, InterventionClass
from core.simulator import simulate, _power_model

with open(Path(__file__).parent.parent / "core" / "config.yaml") as f:
    CFG = yaml.safe_load(f)


class TestPowerModel:
    def test_no_change_returns_one(self):
        assert _power_model(60, 60, 4.0) == pytest.approx(1.0)

    def test_canonical_example_60_to_30(self):
        # (30/60)^4 = 0.5^4 = 0.0625  → 93.75% reduction
        ratio = _power_model(60, 30, 4.0)
        assert ratio == pytest.approx(0.0625, rel=1e-4)

    def test_zero_speed_before_returns_one(self):
        assert _power_model(0, 30, 4.0) == 1.0


class TestSimulate:
    def _inp(self, v1, v2, interv="sign_plus_calming", fatalities=1.0):
        return SimulatorInput(
            segment_id="test",
            speed_before=v1,
            speed_after=v2,
            annual_fatalities=fatalities,
            intervention_class=InterventionClass(interv),
        )

    def test_sign_only_gives_zero_reduction(self):
        result = simulate(self._inp(60, 30, interv="sign_only"), CFG)
        assert result.fatalities_reduction_pct == pytest.approx(0.0)
        assert result.speed_effective_after == pytest.approx(60.0)

    def test_redesign_gives_full_reduction(self):
        result = simulate(self._inp(60, 30, interv="redesign"), CFG)
        assert result.fatalities_reduction_pct == pytest.approx(93.75, rel=1e-3)

    def test_calming_gives_partial_reduction(self):
        # 85% of (60→30) = effective speed = 60 - 30*0.85 = 34.5
        result = simulate(self._inp(60, 30, interv="sign_plus_calming"), CFG)
        assert result.speed_effective_after == pytest.approx(34.5, rel=1e-3)
        assert 0 < result.fatalities_reduction_pct < 93.75

    def test_lives_saved_computed_when_fatalities_nonzero(self):
        result = simulate(self._inp(60, 30, interv="redesign", fatalities=2.0), CFG)
        assert result.lives_saved_per_year is not None
        assert result.lives_saved_per_year == pytest.approx(2.0 * 0.9375, rel=1e-3)

    def test_no_speed_increase(self):
        # Increasing speed should produce negative reduction (or zero handled)
        result = simulate(self._inp(30, 60, interv="redesign"), CFG)
        assert result.fatalities_reduction_pct < 0  # more fatalities
