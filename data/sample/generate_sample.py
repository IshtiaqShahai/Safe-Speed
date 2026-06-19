"""Generate a synthetic Peshawar road network sample for demo purposes.

Produces data/sample/peshawar_sample.geojson with ~40 road segments
covering all four diagnosis categories and a range of confidence levels.

Run: python data/sample/generate_sample.py
"""
import json
import random
from pathlib import Path

random.seed(42)

# Peshawar approximate bounding box: 33.95–34.07°N, 71.45–71.65°E
LAT_MIN, LAT_MAX = 33.95, 34.07
LON_MIN, LON_MAX = 71.45, 71.65

OUTPUT_PATH = Path(__file__).parent / "peshawar_sample.geojson"


def rand_coord():
    return [
        round(random.uniform(LON_MIN, LON_MAX), 5),
        round(random.uniform(LAT_MIN, LAT_MAX), 5),
    ]


def line_geom():
    start = rand_coord()
    end = [
        round(start[0] + random.uniform(-0.005, 0.005), 5),
        round(start[1] + random.uniform(-0.005, 0.005), 5),
    ]
    return {"type": "LineString", "coordinates": [start, end]}


ROAD_NAMES = [
    "Namak Mandi Chowk", "GT Road Connector", "Charsadda Road", "Ring Road N",
    "Khyber Bazaar St", "University Road", "Jamrud Road", "Hayatabad Link",
    "Warsak Road Segment", "Bara Road S", "Peshawar Cantt Rd", "Sunehri Masjid Rd",
    "Dabgari Garden Rd", "Firdous Cinema Rd", "Saddar Bazaar St", "Qissa Khwani Bazaar",
    "Kohat Road Seg", "Rashid Garhi Rd", "Phase 4 Connector", "Tehkal Road",
    "Pishtakhara Bridge Rd", "Old City Core", "Industrial Estate Rd", "Airport Link",
    "Circular Road West", "Model Town Rd", "Shami Road", "Pir Zakori Road",
    "Gulbahar Chowk", "Kotwali Road", "Dalazak Road", "Hangu Road Seg",
    "Nasir Bagh Road", "Islamia Road", "Museum Road", "Services Hospital Rd",
    "Arbab Road", "Khanjee Road", "Kacha Garhi Rd", "Matni Road",
]

# Pre-defined scenario templates to ensure all categories appear
SCENARIOS = [
    # (label, is_divided, has_footpath, school, market, transit, intersection_density,
    #  posted_speed, p85_speed, aadt, ptw_share, probe_count, road_class)
    # ── UNSAFE LIMIT (S_posted > S_safe) ──
    {"diag_hint": "unsafe", "is_divided": False, "has_footpath": False,
     "school": True, "market": True, "transit": True,
     "intersection_density": 3.0, "posted_speed": 60, "p85_speed": 58,
     "aadt": 12000, "ptw_share": 0.35, "probe_count": 80, "road_class": "secondary"},

    {"diag_hint": "unsafe", "is_divided": False, "has_footpath": False,
     "school": False, "market": True, "transit": True,
     "intersection_density": 2.0, "posted_speed": 50, "p85_speed": 52,
     "aadt": 9000, "ptw_share": 0.32, "probe_count": 65, "road_class": "secondary"},

    {"diag_hint": "unsafe", "is_divided": False, "has_footpath": False,
     "school": True, "market": False, "transit": True,
     "intersection_density": 5.0, "posted_speed": 60, "p85_speed": 55,
     "aadt": 8000, "ptw_share": 0.20, "probe_count": 90, "road_class": "primary"},

    {"diag_hint": "unsafe", "is_divided": False, "has_footpath": True,
     "school": True, "market": True, "transit": False,
     "intersection_density": 2.5, "posted_speed": 50, "p85_speed": 47,
     "aadt": 15000, "ptw_share": 0.18, "probe_count": 120, "road_class": "primary"},

    # ── NON-CREDIBLE (P85 >> S_posted, but S_posted ≤ S_safe) ──
    {"diag_hint": "noncred", "is_divided": True, "has_footpath": True,
     "school": False, "market": False, "transit": False,
     "intersection_density": 1.0, "posted_speed": 60, "p85_speed": 80,
     "aadt": 20000, "ptw_share": 0.08, "probe_count": 200, "road_class": "trunk"},

    {"diag_hint": "noncred", "is_divided": False, "has_footpath": True,
     "school": False, "market": False, "transit": False,
     "intersection_density": 1.5, "posted_speed": 50, "p85_speed": 68,
     "aadt": 18000, "ptw_share": 0.10, "probe_count": 150, "road_class": "primary"},

    {"diag_hint": "noncred", "is_divided": True, "has_footpath": True,
     "school": False, "market": False, "transit": True,
     "intersection_density": 0.8, "posted_speed": 80, "p85_speed": 100,
     "aadt": 30000, "ptw_share": 0.05, "probe_count": 300, "road_class": "trunk"},

    # ── DESIGN-ENABLED RISK (P85 ≈ S_posted, both > S_safe) ──
    {"diag_hint": "design", "is_divided": False, "has_footpath": False,
     "school": True, "market": True, "transit": True,
     "intersection_density": 3.5, "posted_speed": 40, "p85_speed": 42,
     "aadt": 10000, "ptw_share": 0.40, "probe_count": 75, "road_class": "secondary"},

    {"diag_hint": "design", "is_divided": False, "has_footpath": False,
     "school": False, "market": True, "transit": True,
     "intersection_density": 4.5, "posted_speed": 50, "p85_speed": 51,
     "aadt": 8500, "ptw_share": 0.30, "probe_count": 60, "road_class": "secondary"},

    # ── SAFE ──
    {"diag_hint": "safe", "is_divided": True, "has_footpath": True,
     "school": False, "market": False, "transit": False,
     "intersection_density": 0.5, "posted_speed": 80, "p85_speed": 76,
     "aadt": 25000, "ptw_share": 0.06, "probe_count": 250, "road_class": "motorway"},

    {"diag_hint": "safe", "is_divided": False, "has_footpath": True,
     "school": False, "market": False, "transit": True,
     "intersection_density": 3.0, "posted_speed": 50, "p85_speed": 47,
     "aadt": 12000, "ptw_share": 0.15, "probe_count": 100, "road_class": "primary"},

    {"diag_hint": "safe", "is_divided": False, "has_footpath": True,
     "school": True, "market": False, "transit": False,
     "intersection_density": 2.0, "posted_speed": 30, "p85_speed": 28,
     "aadt": 4000, "ptw_share": 0.20, "probe_count": 55, "road_class": "residential"},

    # ── LOW CONFIDENCE / INSUFFICIENT ──
    {"diag_hint": "insuff", "is_divided": False, "has_footpath": False,
     "school": False, "market": False, "transit": False,
     "intersection_density": 2.0, "posted_speed": 60, "p85_speed": None,
     "aadt": None, "ptw_share": 0.10, "probe_count": 3, "road_class": "secondary"},
]

# Fill remaining segments with random plausible data
def random_scenario(idx):
    is_divided = random.random() < 0.25
    has_footpath = random.random() < 0.35
    school = random.random() < 0.3
    market = random.random() < 0.35
    transit = random.random() < 0.4
    road_classes = ["primary", "secondary", "tertiary", "residential", "trunk"]
    road_class = random.choice(road_classes)
    intersection_density = round(random.uniform(0.5, 7.0), 1)
    posted_speed = random.choice([30, 40, 50, 60, 70, 80])
    p85_speed = round(posted_speed + random.uniform(-10, 25), 0) if random.random() > 0.1 else None
    aadt = round(random.uniform(1000, 40000), 0) if random.random() > 0.1 else None
    ptw_share = round(random.uniform(0.05, 0.45), 2)
    probe_count = random.choice([0, 2, 5, 15, 40, 80, 150, 200])
    return {
        "diag_hint": "random",
        "is_divided": is_divided,
        "has_footpath": has_footpath,
        "school": school,
        "market": market,
        "transit": transit,
        "intersection_density": intersection_density,
        "posted_speed": posted_speed,
        "p85_speed": p85_speed,
        "aadt": aadt,
        "ptw_share": ptw_share,
        "probe_count": probe_count,
        "road_class": road_class,
    }


def build_features():
    features = []
    all_scenarios = SCENARIOS + [random_scenario(i) for i in range(27)]
    names = ROAD_NAMES + [f"Segment {i}" for i in range(len(all_scenarios) - len(ROAD_NAMES))]

    for i, sc in enumerate(all_scenarios):
        geom = line_geom()
        mid = geom["coordinates"][0]
        feat = {
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "segment_id": f"psh_{i+1:04d}",
                "road_name": names[i],
                "city": "Peshawar",
                "country": "PK",
                "urban": True,
                "lon": mid[0],
                "lat": mid[1],
                "length_m": round(random.uniform(200, 1500), 0),
                "road_class": sc["road_class"],
                "is_divided": sc["is_divided"],
                "has_footpath": sc["has_footpath"],
                "school_within_200m": sc["school"],
                "market_within_200m": sc["market"],
                "transit_stop_within_100m": sc["transit"],
                "intersection_density": sc["intersection_density"],
                "posted_speed": sc["posted_speed"],
                "p85_speed": sc["p85_speed"],
                "aadt": sc["aadt"],
                "ptw_share": sc["ptw_share"],
                "probe_count": sc["probe_count"],
                "posted_speed_source": "adb" if sc["probe_count"] > 10 else "osm",
                "sign_conflict": random.random() < 0.08,
            }
        }
        features.append(feat)
    return features


if __name__ == "__main__":
    features = build_features()
    geojson = {"type": "FeatureCollection", "features": features}
    OUTPUT_PATH.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    print(f"Generated {len(features)} segments -> {OUTPUT_PATH}")
