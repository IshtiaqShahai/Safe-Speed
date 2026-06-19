"""Data Triage Agent — Stage 1 AI layer.

Audits dataset completeness and writes a structured quality report with
recommendations.  Choices are restricted to a predefined fallback whitelist;
the agent cannot invent data or change numerical values.

Runs before the deterministic pipeline; its output informs the ingest stage
about which fallback chains to activate.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

FALLBACK_WHITELIST = {
    "posted_speed": ["adb", "mapillary", "osm", "estimated"],
    "p85_speed": ["adb_probe", "none"],
    "aadt": ["adb", "estimated_from_class", "none"],
    "vru_context": ["adb_context", "osm_amenities", "worldpop", "none"],
}


def run_triage(
    quality_report: dict,
    cfg: dict,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """Run the Data Triage Agent to produce a structured remediation plan.

    Returns a dict with:
      - summary: one-paragraph assessment
      - fallback_decisions: {field: chosen_source}
      - warnings: list of issues
      - recommendation: next action
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("ANTHROPIC_API_KEY not set — using deterministic triage.")
        return _deterministic_triage(quality_report, cfg)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.warning("anthropic SDK not installed — using deterministic triage.")
        return _deterministic_triage(quality_report, cfg)

    prompt = _build_triage_prompt(quality_report, cfg)

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text

    try:
        # Extract JSON block if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        result = json.loads(raw)
    except (json.JSONDecodeError, IndexError):
        result = {"summary": raw, "fallback_decisions": {}, "warnings": []}

    # Enforce whitelist on fallback decisions
    for field, chosen in list(result.get("fallback_decisions", {}).items()):
        allowed = FALLBACK_WHITELIST.get(field, [])
        if chosen not in allowed:
            logger.warning(
                f"Triage agent chose non-whitelisted fallback '{chosen}' for '{field}'. "
                f"Reverting to first option: '{allowed[0] if allowed else 'none'}'"
            )
            result["fallback_decisions"][field] = allowed[0] if allowed else "none"

    return result


def _build_triage_prompt(quality_report: dict, cfg: dict) -> str:
    return f"""You are a Data Triage Agent for a road safety analysis system.

Your task: review the data quality report below and produce a JSON remediation plan.
You may ONLY choose fallback sources from these whitelists:
{json.dumps(FALLBACK_WHITELIST, indent=2)}

Data quality report:
{json.dumps(quality_report, indent=2)}

Respond ONLY with valid JSON matching this schema:
{{
  "summary": "<one paragraph assessment>",
  "fallback_decisions": {{
    "<field>": "<chosen_source_from_whitelist>"
  }},
  "warnings": ["<issue1>", ...],
  "recommendation": "<next action>"
}}
"""


def _deterministic_triage(quality_report: dict, cfg: dict) -> dict:
    """Rule-based triage when no API key is available."""
    warnings = quality_report.get("warnings", [])
    n = quality_report.get("total_segments", 0)
    has_posted_pct = 100 * quality_report.get("segments_with_posted_speed", 0) / max(n, 1)
    probe_pct = quality_report.get("probe_coverage_pct", 0)

    fallbacks = {}
    if has_posted_pct < 70:
        fallbacks["posted_speed"] = "osm"
    else:
        fallbacks["posted_speed"] = "adb"

    if probe_pct < 20:
        fallbacks["p85_speed"] = "none"
        warnings.append("Insufficient probe data; P85-dependent diagnoses will be limited.")
    else:
        fallbacks["p85_speed"] = "adb_probe"

    fallbacks["vru_context"] = "adb_context"

    return {
        "summary": (
            f"Dataset has {has_posted_pct:.0f}% posted-speed coverage and "
            f"{probe_pct:.0f}% probe coverage. "
            "Deterministic fallback chain applied."
        ),
        "fallback_decisions": fallbacks,
        "warnings": warnings,
        "recommendation": "Proceed with pipeline using selected fallbacks.",
    }
