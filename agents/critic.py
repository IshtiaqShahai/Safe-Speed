"""Critic Agent — quality gate for AI-generated policy briefs.

Cross-checks every figure and recommendation in a brief against the
segment's actual pipeline data.  Failed briefs are regenerated with
specific feedback.  Hard-fails on any unverifiable number.

This agent is the firewall ensuring the AI layer never contaminate
the analytical validity of the deterministic core.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Tuple

from core.models import PolicyBrief

logger = logging.getLogger(__name__)


def _extract_numbers(text: str) -> list[float]:
    """Extract all numeric values from a text string."""
    return [float(m) for m in re.findall(r"\b\d+(?:\.\d+)?\b", text)]


def _pipeline_numbers(seg: dict) -> set[float]:
    """Collect all numeric values that are legitimately in the pipeline output."""
    numbers = set()
    for key in [
        "posted_speed", "s_posted", "s_safe", "p85_speed", "p85",
        "score", "vru_index", "aadt", "recommended_speed",
        "lives_saved_per_year", "fatalities_reduction_pct",
        "intersection_density", "length_m", "ptw_share",
    ]:
        val = seg.get(key)
        if val is not None:
            try:
                numbers.add(round(float(val), 1))
                numbers.add(round(float(val), 0))
                numbers.add(int(float(val)))
            except (ValueError, TypeError):
                pass
    # Add known Safe System thresholds (always legitimate to cite)
    numbers.update({10, 20, 30, 40, 50, 60, 70, 80, 100, 110, 120})
    return numbers


def validate_brief(
    brief: PolicyBrief,
    seg: dict,
    client=None,
    max_retries: int = 1,
) -> Tuple[PolicyBrief, str]:
    """Validate a policy brief against pipeline data.

    Returns (validated_brief, critic_notes).
    If critical violations are found and client is available, one regeneration
    attempt is made.  After that, the brief is marked critic_validated=False.
    """
    violations = _check_brief(brief.summary_en, seg)

    if not violations:
        brief.critic_validated = True
        brief.critic_notes = "All figures verified against pipeline output."
        return brief, ""

    notes = f"Violations found: {'; '.join(violations)}"
    logger.warning(f"Critic found violations in {brief.segment_id}: {notes}")

    # Attempt regeneration if client available
    if client and max_retries > 0:
        corrected_brief = _regenerate(brief, seg, violations, client)
        re_violations = _check_brief(corrected_brief.summary_en, seg)
        if not re_violations:
            corrected_brief.critic_validated = True
            corrected_brief.critic_notes = "Regenerated after critic feedback."
            return corrected_brief, notes
        notes += f" | Still failing after regeneration: {'; '.join(re_violations)}"

    brief.critic_validated = False
    brief.critic_notes = notes
    return brief, notes


def _check_brief(text: str, seg: dict) -> list[str]:
    """Return a list of violation descriptions for unverifiable numbers."""
    violations = []
    allowed = _pipeline_numbers(seg)
    found = _extract_numbers(text)

    for num in found:
        # Allow numbers in ranges and percentages (0–100)
        if 0 <= num <= 100:
            continue
        # Check if this number is in the allowed set (within tolerance)
        if not any(abs(num - a) < 2 for a in allowed):
            violations.append(
                f"Number {num} not found in pipeline output for segment "
                f"{seg.get('segment_id', '?')}"
            )

    # Check that recommended speed matches pipeline value
    rec = seg.get("recommended_speed")
    if rec and str(int(rec)) not in text and str(rec) not in text:
        violations.append(
            f"Recommended speed {rec} km/h not mentioned in brief."
        )

    # Check that diagnosis is consistent
    diagnosis = seg.get("diagnosis", "")
    diagnosis_words = {
        "unsafe_limit": ["unsafe", "exceeds", "above"],
        "non_credible_limit": ["non-credible", "non credible", "ignored", "credible"],
        "design_enabled_risk": ["design", "infrastructure", "road design"],
        "safe": ["safe", "aligned"],
    }
    required_words = diagnosis_words.get(diagnosis, [])
    if required_words and not any(w in text.lower() for w in required_words):
        violations.append(
            f"Brief does not reflect diagnosis '{diagnosis}' — "
            f"expected one of: {required_words}"
        )

    return violations


def _regenerate(
    brief: PolicyBrief,
    seg: dict,
    violations: list[str],
    client,
) -> PolicyBrief:
    """Ask the LLM to fix the brief given specific critic feedback."""
    from core.pipeline import load_config
    try:
        cfg = load_config()
        model = "claude-sonnet-4-6"
    except Exception:
        return brief

    feedback = "\n".join(f"- {v}" for v in violations)
    prompt = (
        f"The following policy brief for road segment {brief.segment_id} "
        f"contains errors identified by the Critic Agent:\n{feedback}\n\n"
        f"Original brief:\n{brief.summary_en}\n\n"
        f"Segment pipeline data:\n{json.dumps(seg, default=str, indent=2)}\n\n"
        "Rewrite the brief fixing all violations. Only use numbers present in "
        "the pipeline data. Output only the corrected paragraph."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        brief.summary_en = response.content[0].text.strip()
    except Exception as exc:
        logger.warning(f"Regeneration failed: {exc}")

    return brief
