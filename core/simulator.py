"""Nilsson–Elvik Lives-Saved Simulator.

Answers: if operating speed changes from v1 to v2, what happens to deaths?

Core model:
  Fatalities_after = Fatalities_before × (v2_effective / v1)^e

Honesty rule (§2.3, §11): a sign change ALONE is NOT assumed to change P85.
Speed-change effectiveness per intervention class is declared in config.yaml.

References:
  Nilsson (2004) Traffic Safety Dimensions and the Power Model — Lund Univ.
  Elvik (2013) Re-parameterisation of the Power Model — AAP.
  Elvik, Vadeby, Hels, van Schagen (2019) Updated estimates — AAP.
"""
from __future__ import annotations
from typing import Optional
from .models import SimulatorInput, SimulatorResult, InterventionClass


def _power_model(v_before: float, v_after: float, exponent: float) -> float:
    """Return proportion of crashes remaining after speed change."""
    if v_before <= 0:
        return 1.0
    return (v_after / v_before) ** exponent


def simulate(inp: SimulatorInput, cfg: dict) -> SimulatorResult:
    """Run the Nilsson–Elvik model for a single segment intervention.

    Returns crash reduction percentages and (optionally) absolute lives saved.
    """
    scfg = cfg["simulator"]
    effectiveness = scfg["speed_change_effectiveness"][inp.intervention_class]

    # Effective operating speed after intervention
    delta = inp.speed_before - inp.speed_after
    v_eff_after = inp.speed_before - delta * effectiveness

    e_fatal = scfg["exponents"]["fatality"]
    e_serious = scfg["exponents"]["serious_injury"]
    e_all = scfg["exponents"]["all_injury"]

    ratio_fatal = _power_model(inp.speed_before, v_eff_after, e_fatal)
    ratio_serious = _power_model(inp.speed_before, v_eff_after, e_serious)
    ratio_all = _power_model(inp.speed_before, v_eff_after, e_all)

    fatal_red_pct = round((1 - ratio_fatal) * 100, 2)
    serious_red_pct = round((1 - ratio_serious) * 100, 2)
    all_red_pct = round((1 - ratio_all) * 100, 2)

    lives_saved: Optional[float] = None
    serious_saved: Optional[float] = None
    if inp.annual_fatalities > 0:
        lives_saved = round(inp.annual_fatalities * (1 - ratio_fatal), 3)
    if inp.annual_serious_injuries > 0:
        serious_saved = round(inp.annual_serious_injuries * (1 - ratio_serious), 3)

    return SimulatorResult(
        segment_id=inp.segment_id,
        speed_before=inp.speed_before,
        speed_effective_after=round(v_eff_after, 1),
        fatalities_reduction_pct=fatal_red_pct,
        serious_injury_reduction_pct=serious_red_pct,
        all_injury_reduction_pct=all_red_pct,
        intervention_class=inp.intervention_class,
        lives_saved_per_year=lives_saved,
        serious_injuries_saved_per_year=serious_saved,
    )


def simulate_network(
    segments: list[dict],
    cfg: dict,
    annual_fatality_rate_per_km: float = 0.05,
) -> list[SimulatorResult]:
    """Convenience wrapper — simulate unsafe_limit segments where we lower the limit.

    Only runs for 'unsafe_limit' diagnosis (posted > S_safe). Non-credible and
    design-enabled risk require road redesign, not a limit change, so the
    Nilsson–Elvik limit-change model does not apply and is skipped for those.
    """
    results = []
    for seg in segments:
        if seg.get("diagnosis") != "unsafe_limit":
            continue
        s_posted = seg.get("posted_speed") or seg.get("s_posted")
        rec = seg.get("recommended_speed")
        if not s_posted or not rec:
            continue
        # Safety guard: never model a speed increase
        if float(rec) >= float(s_posted):
            continue
        length_km = seg.get("length_m", 500) / 1000
        inp = SimulatorInput(
            segment_id=seg["segment_id"],
            speed_before=float(s_posted),
            speed_after=float(rec),
            annual_fatalities=round(length_km * annual_fatality_rate_per_km, 4),
            intervention_class=InterventionClass(
                seg.get("intervention_class", "sign_plus_calming")
            ),
        )
        results.append(simulate(inp, cfg))
    return results
