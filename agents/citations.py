"""Evidence-Citation Agent — RAG over a closed, versioned reference corpus.

Retrieves supporting passages for every claim in a policy brief so that
panel output carries verifiable sources.  Uses in-memory vector search
(sentence-transformers + cosine similarity) when Qdrant is unavailable.

No open-web search is performed; the corpus is fixed and versioned.
"""
from __future__ import annotations
import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

# ── Closed reference corpus ───────────────────────────────────────────────────
# Each entry: (short_id, full_citation, key_claim, passage)

CORPUS: list[dict] = [
    {
        "id": "WHO_GRSF_30",
        "citation": "WHO/GRSF Speed Management Manual (2008), p.27",
        "claim": "30 km/h VRU mixing survivability",
        "passage": (
            "Where motor vehicles share space with pedestrians, cyclists, and "
            "powered two-wheelers without physical separation, speeds must not "
            "exceed 30 km/h for crashes to remain survivable."
        ),
    },
    {
        "id": "WHO_GRSF_50",
        "citation": "WHO/GRSF Speed Management Manual (2008), p.28",
        "claim": "50 km/h intersection side-impact survivability",
        "passage": (
            "At uncontrolled intersections where side-impact collisions are "
            "possible, impact speeds above 50 km/h are unlikely to be survived "
            "by vehicle occupants."
        ),
    },
    {
        "id": "STOCKHOLM_30",
        "citation": "Stockholm Declaration (2020), §11",
        "passage": (
            "We commit to set a default urban speed limit of 30 km/h, except "
            "on roads where it can be shown that higher speeds are safe, in "
            "order to protect vulnerable road users."
        ),
        "claim": "30 km/h default urban mandate",
    },
    {
        "id": "NILSSON_2004",
        "citation": "Nilsson, G. (2004). Traffic Safety Dimensions and the Power Model. Lund University.",
        "claim": "Power model fatality exponent 4",
        "passage": (
            "The relationship between speed and the number of fatal accidents "
            "follows a power function with exponent approximately 4: "
            "Nf2/Nf1 = (v2/v1)^4."
        ),
    },
    {
        "id": "ELVIK_2013",
        "citation": "Elvik, R. (2013). A re-parameterisation of the Power Model. AAP 50.",
        "claim": "Updated power model exponents",
        "passage": (
            "Re-estimation yields fatality exponent 3.6–4.5 depending on road "
            "type; serious-injury exponent approximately 3.0; all-injury "
            "exponent approximately 2.0."
        ),
    },
    {
        "id": "GOLDENBELD_2007",
        "citation": "Goldenbeld & van Schagen (2007). The credibility of speed limits. AAP 39(6).",
        "claim": "Non-credible speed limits",
        "passage": (
            "Speed limits are credible when the visual design of the road "
            "is consistent with the posted limit. Limits that contradict road "
            "character are systematically ignored by drivers."
        ),
    },
    {
        "id": "STREET_DESIGN_SPEED_2025",
        "citation": "[Street-design → operating-speed ML study, 2025 — full citation to be verified. Milan/Amsterdam/Dubai dataset.]",
        "claim": "Sign change alone does not reduce P85",
        "passage": (
            "Machine-learning analysis across Milan, Amsterdam, and Dubai "
            "(R²≈0.70) confirms that lowering the posted speed limit without "
            "changing road design does not reliably reduce operating speeds."
        ),
    },
    {
        "id": "IRAP_310",
        "citation": "iRAP Star Rating Methodology, Model 3.10",
        "claim": "iRAP 1-2 star road risk",
        "passage": (
            "Roads rated 1 or 2 stars by iRAP have design deficiencies that "
            "make crashes more likely and more severe.  The Zero-Star band "
            "denotes segments where crashes are almost always fatal or "
            "seriously injuring."
        ),
    },
    {
        "id": "AUSTROADS_SS",
        "citation": "Austroads Guide to Road Safety: Safe System Speeds",
        "claim": "Undivided road 70 km/h head-on survivability",
        "passage": (
            "On undivided roads where head-on collisions are possible, "
            "Safe System speeds are 70 km/h or less to keep crash energy "
            "within human biomechanical tolerance."
        ),
    },
    {
        "id": "WHO_GLOBAL_2030",
        "citation": "WHO/UN Global Plan for Decade of Action 2021–2030",
        "claim": "Safe System approach mandate",
        "passage": (
            "The Safe System approach requires that the road system be "
            "designed so that human error does not result in death or "
            "serious injury — speed management is the highest-priority lever."
        ),
    },
]


# ── Simple in-memory vector search ───────────────────────────────────────────

_embedder = None
_corpus_vectors: Optional[np.ndarray] = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer("BAAI/bge-small-en-v1.5")
            logger.info("Loaded BGE-small embedder for citation search.")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed; using keyword citation fallback."
            )
    return _embedder


def _build_corpus_vectors():
    global _corpus_vectors
    if _corpus_vectors is not None:
        return _corpus_vectors
    embedder = _get_embedder()
    if embedder is None:
        return None
    texts = [f"{c['claim']} {c['passage']}" for c in CORPUS]
    _corpus_vectors = embedder.encode(texts, normalize_embeddings=True)
    return _corpus_vectors


def retrieve_citations(query: str, top_k: int = 3) -> list[dict]:
    """Return top-k citations most relevant to the query.

    Uses semantic search when sentence-transformers is available,
    otherwise falls back to keyword overlap.
    """
    embedder = _get_embedder()
    vectors = _build_corpus_vectors() if embedder else None

    if embedder and vectors is not None:
        q_vec = embedder.encode([query], normalize_embeddings=True)[0]
        scores = np.dot(vectors, q_vec)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [CORPUS[i] for i in top_indices]

    # Keyword fallback
    query_words = set(query.lower().split())
    scored = []
    for entry in CORPUS:
        text_words = set((entry["claim"] + " " + entry["passage"]).lower().split())
        overlap = len(query_words & text_words)
        scored.append((overlap, entry))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [e for _, e in scored[:top_k]]


def format_citation(entry: dict) -> str:
    return f"[{entry['id']}] {entry['citation']}: \"{entry['passage'][:120]}...\""
