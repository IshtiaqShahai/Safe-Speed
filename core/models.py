from __future__ import annotations
from typing import Optional, Dict, List
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class Diagnosis(str, Enum):
    UNSAFE_LIMIT = "unsafe_limit"
    NON_CREDIBLE_LIMIT = "non_credible_limit"
    DESIGN_ENABLED_RISK = "design_enabled_risk"
    SAFE = "safe"
    INSUFFICIENT_DATA = "insufficient_data"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class InterventionClass(str, Enum):
    SIGN_ONLY = "sign_only"
    SIGN_PLUS_CALMING = "sign_plus_calming"
    REDESIGN = "redesign"


class SegmentFeatures(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    segment_id: str
    geometry_wkt: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    length_m: float = 500.0

    # Road characteristics
    road_class: str = "secondary"
    is_divided: bool = False
    has_footpath: bool = False
    intersection_density: float = 0.0     # intersections per km

    # Speed data
    posted_speed: Optional[float] = None  # km/h
    p85_speed: Optional[float] = None     # km/h
    probe_count: int = 0

    # Exposure
    aadt: Optional[float] = None
    ptw_share: float = 0.0                # fraction 0–1

    # VRU context
    school_within_200m: bool = False
    market_within_200m: bool = False
    transit_stop_within_100m: bool = False

    # Source / quality flags
    posted_speed_source: str = "unknown"  # adb | mapillary | osm | estimated
    sign_conflict: bool = False            # tabular limit ≠ Mapillary detection

    # Pipeline outputs (set by stages 3–6)
    s_safe: Optional[float] = None
    s_safe_rule: Optional[str] = None
    vru_index: Optional[float] = None
    score: Optional[float] = None
    confidence: Optional[str] = None
    diagnosis: Optional[str] = None
    recommended_speed: Optional[float] = None
    intervention_class: Optional[str] = None

    # Metadata
    country: str = "PK"
    city: str = "Peshawar"
    urban: bool = True
    road_name: Optional[str] = None


class ScoringResult(BaseModel):
    segment_id: str
    s_safe: float
    s_safe_rule: str
    s_posted: Optional[float]
    p85: Optional[float]
    vru_index: float
    limit_gap: float
    behavior_gap: float
    raw_risk: float
    exposure: float
    score: float
    confidence: Confidence
    diagnosis: Diagnosis
    recommended_speed: float
    intervention_class: InterventionClass


class SimulatorInput(BaseModel):
    segment_id: str
    speed_before: float
    speed_after: float
    annual_fatalities: float = 0.0
    annual_serious_injuries: float = 0.0
    annual_all_injuries: float = 0.0
    intervention_class: InterventionClass = InterventionClass.SIGN_PLUS_CALMING


class SimulatorResult(BaseModel):
    segment_id: str
    speed_before: float
    speed_effective_after: float
    fatalities_reduction_pct: float
    serious_injury_reduction_pct: float
    all_injury_reduction_pct: float
    intervention_class: InterventionClass
    lives_saved_per_year: Optional[float] = None
    serious_injuries_saved_per_year: Optional[float] = None


class DataQualityReport(BaseModel):
    total_segments: int
    segments_with_posted_speed: int
    segments_with_p85: int
    segments_with_aadt: int
    segments_with_footpath_data: int
    probe_coverage_pct: float
    sign_conflict_count: int
    low_confidence_count: int
    medium_confidence_count: int
    high_confidence_count: int
    fallbacks_used: Dict[str, int] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class PolicyBrief(BaseModel):
    segment_id: str
    summary_en: str
    summary_ur: str
    key_findings: List[str] = Field(default_factory=list)
    recommended_intervention: str
    estimated_lives_saved: Optional[float] = None
    cost_class: str = "unknown"   # signage-only | calming | redesign
    citations: List[str] = Field(default_factory=list)
    critic_validated: bool = False
    critic_notes: Optional[str] = None
