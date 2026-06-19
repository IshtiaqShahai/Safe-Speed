import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.simulator import simulate
from core.models import SimulatorInput, InterventionClass
import yaml

with open(Path(__file__).parent.parent / "core" / "config.yaml") as f:
    cfg = yaml.safe_load(f)

cases = [
    ("redesign",          60, 30),
    ("sign_only",         60, 30),
    ("sign_plus_calming", 60, 30),
    ("redesign",          80, 50),
    ("redesign",          50, 50),  # no change — expect 0%
]

print(f"{'Case':<32} {'v_eff':>6} {'fatal%':>8} {'serious%':>10} {'all%':>7}  direction")
print("-" * 80)
all_ok = True
for iclass, vbefore, vafter in cases:
    inp = SimulatorInput(
        segment_id="test",
        speed_before=float(vbefore),
        speed_after=float(vafter),
        annual_fatalities=1.0,
        intervention_class=InterventionClass(iclass),
    )
    r = simulate(inp, cfg)
    tag = f"{iclass} {vbefore}->{vafter}"

    # Direction invariant: if speed drops, fatalities must drop (positive %)
    if vafter < vbefore and iclass != "sign_only":
        ok = r.fatalities_reduction_pct > 0
    elif iclass == "sign_only":
        ok = r.fatalities_reduction_pct == 0.0
    else:
        ok = r.fatalities_reduction_pct == 0.0

    marker = "OK" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"{tag:<32} {r.speed_effective_after:>6} {r.fatalities_reduction_pct:>8} {r.serious_injury_reduction_pct:>10} {r.all_injury_reduction_pct:>7}  {marker}")

print()
# Verify the exact number from the README worked example
inp_readme = SimulatorInput(
    segment_id="readme_example",
    speed_before=60.0,
    speed_after=30.0,
    annual_fatalities=1.0,
    intervention_class=InterventionClass("redesign"),
)
r = simulate(inp_readme, cfg)
expected_fatal_pct = 93.75
match = abs(r.fatalities_reduction_pct - expected_fatal_pct) < 0.01
print(f"README example check: expected {expected_fatal_pct}%, got {r.fatalities_reduction_pct}%  ->  {'MATCH' if match else 'MISMATCH'}")
print(f"All direction checks: {'PASS' if all_ok else 'FAIL'}")
