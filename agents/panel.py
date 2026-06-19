"""Safe System Panel — 5-agent multi-perspective policy brief generator.

Roles:
  1. VRU Advocate     — leads with pedestrian, cyclist, PTW vulnerability
  2. Network Engineer — road design and traffic engineering perspective
  3. Trauma Analyst   — injury biomechanics and crash survivability
  4. Economist        — cost-benefit, lives-per-dollar, budget class
  5. Panel Chair      — synthesises all perspectives into a brief

Each agent may ONLY reference numbers present in the pipeline output.
The Critic Agent validates every figure before the brief is finalised.

Briefs are produced in English and Urdu (via translation pass).
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

from .citations import retrieve_citations, format_citation
from .critic import validate_brief
from core.models import PolicyBrief

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

ROLE_PROMPTS = {
    "vru_advocate": (
        "You are a VRU (Vulnerable Road User) Advocate on a road safety panel. "
        "Focus on the impact on pedestrians, cyclists, and motorcyclists. "
        "Highlight any schools, markets, or transit stops near the segment. "
        "Your paragraph must reference only the numbers provided."
    ),
    "network_engineer": (
        "You are a Road Network Engineer on a road safety panel. "
        "Focus on road geometry (divided/undivided), intersection density, "
        "footpath provision, and what physical changes would reduce risk. "
        "Your paragraph must reference only the numbers provided."
    ),
    "trauma_analyst": (
        "You are a Trauma and Biomechanics Analyst on a road safety panel. "
        "Explain why the current speed limit is or is not survivable using "
        "the Safe System biomechanical thresholds (30/50/70 km/h). "
        "Your paragraph must reference only the numbers provided."
    ),
    "economist": (
        "You are a Transport Economist on a road safety panel. "
        "Describe the cost class of the recommended intervention (signage-only, "
        "traffic calming, or full redesign) and the estimated lives saved per year. "
        "Your paragraph must reference only the numbers provided."
    ),
    "panel_chair": (
        "You are the Panel Chair. Synthesise the four role perspectives into "
        "a single, plain-language policy brief paragraph of 3–4 sentences "
        "suitable for a district engineer or deputy commissioner. "
        "Recommend the specific speed limit change and intervention class. "
        "Do not introduce any number not already established by the other roles."
    ),
}


def _call_llm(system: str, user: str, client) -> str:
    import anthropic
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def _translate_to_urdu(text: str, client) -> str:
    prompt = (
        "Translate the following road-safety policy brief paragraph into Urdu. "
        "Keep technical terms (km/h, S_safe, VRU) in their original form. "
        "Output only the Urdu translation, no commentary.\n\n" + text
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _build_segment_context(seg: dict) -> str:
    """Serialise pipeline output as a structured context block for agents."""
    fields = [
        ("Segment ID", seg.get("segment_id")),
        ("Road name", seg.get("road_name", "Unknown")),
        ("City", seg.get("city", "Peshawar")),
        ("Posted speed (S_posted)", f"{seg.get('posted_speed') or seg.get('s_posted')} km/h"),
        ("Safe System speed (S_safe)", f"{seg.get('s_safe')} km/h"),
        ("S_safe rule fired", seg.get("s_safe_rule")),
        ("85th-percentile speed (P85)", f"{seg.get('p85_speed') or seg.get('p85')} km/h"),
        ("Diagnosis", seg.get("diagnosis")),
        ("Speed Safety Score", seg.get("score")),
        ("Confidence", seg.get("confidence")),
        ("Recommended speed", f"{seg.get('recommended_speed')} km/h"),
        ("Intervention class", seg.get("intervention_class")),
        ("VRU index", seg.get("vru_index")),
        ("AADT", seg.get("aadt")),
        ("School within 200 m", seg.get("school_within_200m")),
        ("Market within 200 m", seg.get("market_within_200m")),
        ("Has footpath", seg.get("has_footpath")),
        ("PTW share", f"{(seg.get('ptw_share') or 0) * 100:.0f}%"),
        ("Is divided carriageway", seg.get("is_divided")),
        ("Estimated lives saved/year", seg.get("lives_saved_per_year")),
    ]
    return "\n".join(f"  {k}: {v}" for k, v in fields if v is not None)


def run_panel(
    segments: list[dict],
    cfg: dict,
    max_retries: int = 2,
) -> list[PolicyBrief]:
    """Generate critic-validated policy briefs for a list of priority segments."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("No API key — skipping AI panel.")
        return []

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.warning("anthropic SDK not installed — skipping AI panel.")
        return []

    briefs: list[PolicyBrief] = []

    for seg in segments:
        seg_id = seg.get("segment_id", "unknown")
        logger.info(f"Panel generating brief for {seg_id}")
        context = _build_segment_context(seg)

        # ── Four role perspectives ────────────────────────────────────────
        role_outputs: dict[str, str] = {}
        for role, system_prompt in list(ROLE_PROMPTS.items())[:-1]:  # skip chair
            user_msg = (
                f"Segment data:\n{context}\n\n"
                "Write ONE paragraph from your role perspective."
            )
            try:
                role_outputs[role] = _call_llm(system_prompt, user_msg, client)
            except Exception as exc:
                logger.warning(f"Role {role} failed for {seg_id}: {exc}")
                role_outputs[role] = f"[{role} perspective unavailable]"

        # ── Panel Chair synthesis ─────────────────────────────────────────
        synthesis_user = (
            f"Segment data:\n{context}\n\n"
            "Role perspectives:\n"
            + "\n\n".join(f"[{r.upper()}]\n{t}" for r, t in role_outputs.items())
            + "\n\nSynthesize into the final policy brief paragraph."
        )
        for attempt in range(max_retries):
            try:
                summary_en = _call_llm(
                    ROLE_PROMPTS["panel_chair"], synthesis_user, client
                )
                break
            except Exception as exc:
                logger.warning(f"Chair synthesis attempt {attempt+1} failed: {exc}")
                summary_en = "Policy brief generation failed."

        # ── Citations ─────────────────────────────────────────────────────
        query = f"{seg.get('diagnosis')} {seg.get('s_safe_rule')} speed safety"
        top_citations = retrieve_citations(query, top_k=3)
        citation_strings = [format_citation(c) for c in top_citations]
        citation_ids = [c["id"] for c in top_citations]

        # ── Urdu translation ──────────────────────────────────────────────
        try:
            summary_ur = _translate_to_urdu(summary_en, client)
        except Exception:
            summary_ur = "[Urdu translation unavailable]"

        # ── Critic validation ─────────────────────────────────────────────
        brief_draft = PolicyBrief(
            segment_id=seg_id,
            summary_en=summary_en,
            summary_ur=summary_ur,
            key_findings=list(role_outputs.values()),
            recommended_intervention=str(seg.get("intervention_class", "sign_only")),
            estimated_lives_saved=seg.get("lives_saved_per_year"),
            cost_class=str(seg.get("intervention_class", "sign_only")),
            citations=citation_ids,
            critic_validated=False,
        )

        validated_brief, notes = validate_brief(brief_draft, seg, client)
        briefs.append(validated_brief)
        if notes:
            logger.info(f"Critic notes for {seg_id}: {notes}")

    return briefs
