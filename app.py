"""
SafeSpeed — A Digital Safe System Panel by Ventax AI Lab
ADB AI for Safer Roads 2026 | Peshawar, Pakistan

Displays scored road segments colour-coded by Safe System diagnosis.
Supports default ADB data or user-uploaded CSV / GeoJSON.
No API key required.
"""
import json
import io
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from pathlib import Path

st.set_page_config(
    page_title="SafeSpeed",
    page_icon="🛡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Dark theme CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Global ── */
html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: #161b2e !important;
    border-right: 1px solid #1e2a45;
}
[data-testid="stSidebar"] * { color: #cbd5e1 !important; }
[data-testid="stSidebar"] h1 {
    color: #7dd3fc !important;
    font-size: 1.6rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.5px;
}
[data-testid="stSidebar"] .stRadio label,
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stCheckbox label {
    color: #94a3b8 !important;
    font-size: 0.78rem !important;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
[data-testid="stSidebar"] hr { border-color: #1e2a45 !important; }

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: #161b2e;
    border: 1px solid #1e2a45;
    border-radius: 10px;
    padding: 16px 20px !important;
}
[data-testid="metric-container"] label {
    color: #64748b !important;
    font-size: 0.75rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    color: #e2e8f0 !important;
    font-size: 1.9rem !important;
    font-weight: 700 !important;
}

/* ── Main background ── */
.stApp { background-color: #0f1117; }
.block-container { padding-top: 1.2rem !important; }

/* ── Divider ── */
hr { border-color: #1e2a45 !important; }

/* ── Upload widget ── */
[data-testid="stFileUploader"] {
    background: #161b2e;
    border: 1px dashed #1e3a5f;
    border-radius: 8px;
    padding: 8px;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────
DIAG_COLOUR = {
    "unsafe_limit":       [220, 38,  38,  230],
    "non_credible_limit": [234, 128,   8,  210],
    "safe":               [ 34, 197,  94,  130],
}
DEFAULT_COL             = [150, 150, 150, 100]
HIGH_PRIORITY_THRESHOLD = 15.0

REQUIRED_COLS = {"lat", "lon", "score", "diagnosis"}

OPTIONAL_DEFAULTS = {
    "segment_id":           "—",
    "city":                 "Uploaded",
    "country":              "—",
    "posted_speed":         float("nan"),
    "p85_speed":            float("nan"),
    "s_safe":               float("nan"),
    "s_safe_rule":          None,
    "recommended_speed":    float("nan"),
    "confidence":           "—",
    "lives_saved_per_year": 0.0,
    "road_class":           "—",
    "helmet_passenger_spi": float("nan"),
    "length_m":             float("nan"),
}


# ── Data helpers ──────────────────────────────────────────────────────────────
def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df["score"]                = pd.to_numeric(df["score"],                errors="coerce").fillna(0)
    df["lives_saved_per_year"] = pd.to_numeric(df["lives_saved_per_year"], errors="coerce").fillna(0)
    df["posted_speed"]         = pd.to_numeric(df["posted_speed"],         errors="coerce")
    df["p85_speed"]            = pd.to_numeric(df["p85_speed"],            errors="coerce")
    df["s_safe"]               = pd.to_numeric(df["s_safe"],               errors="coerce")
    df["high_priority"]        = df["score"] >= HIGH_PRIORITY_THRESHOLD

    for ch, idx in [("r", 0), ("g", 1), ("b", 2), ("a", 3)]:
        df[ch] = df["diagnosis"].map(lambda d, i=idx: DIAG_COLOUR.get(d, DEFAULT_COL)[i])

    df["radius"] = np.where(
        df["diagnosis"] == "unsafe_limit",       300 + df["score"].clip(0, 100) * 18,
        np.where(
        df["diagnosis"] == "non_credible_limit", 200 + df["score"].clip(0, 100) * 10,
        80)
    ).astype(int)

    df["posted_speed_str"] = df["posted_speed"].apply(
        lambda x: f"{int(x)} km/h" if pd.notna(x) else "unknown")
    df["p85_speed_str"] = df["p85_speed"].apply(
        lambda x: f"{int(x)} km/h" if pd.notna(x) else "no probe data")
    df["s_safe_str"] = df["s_safe"].apply(
        lambda x: f"{int(x)} km/h" if pd.notna(x) else "—")
    df["rule_str"]  = df["s_safe_rule"].fillna("—") if "s_safe_rule" in df.columns else "—"
    df["lives_str"] = df["lives_saved_per_year"].apply(
        lambda x: f"+{x:.1f}/yr" if x > 0 else "—")
    return df


def _fill_optional(df: pd.DataFrame) -> pd.DataFrame:
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    return df


@st.cache_data(show_spinner="Loading ADB segment data …")
def load_default() -> pd.DataFrame:
    df = pd.read_csv(Path("docs/map_data.csv"), low_memory=False)
    return _prepare(_fill_optional(df))


def load_csv(file) -> pd.DataFrame:
    df = pd.read_csv(file, low_memory=False)
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        st.error(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        st.stop()
    return _prepare(_fill_optional(df))


def load_geojson(file) -> pd.DataFrame:
    raw = json.load(io.TextIOWrapper(file, encoding="utf-8"))
    features = raw.get("features", [])
    if not features:
        st.error("GeoJSON has no features.")
        st.stop()
    rows = []
    for feat in features:
        p = feat.get("properties") or {}
        g = feat.get("geometry") or {}
        coords = g.get("coordinates", [])
        if g.get("type") == "Point" and len(coords) >= 2:
            p["lon"], p["lat"] = coords[0], coords[1]
        elif g.get("type") == "LineString" and coords:
            mid = coords[len(coords) // 2]
            p["lon"], p["lat"] = mid[0], mid[1]
        rows.append(p)
    df = pd.DataFrame(rows)
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        st.error(f"GeoJSON properties missing required fields: {', '.join(sorted(missing))}")
        st.stop()
    return _prepare(_fill_optional(df))


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("SafeSpeed")
    st.markdown(
        "<p style='color:#94a3b8;font-size:0.95rem;margin-top:-10px;line-height:1.6'>"
        "A Digital Safe System by Ventax AI Lab<br>"
        "<span style='color:#64748b;font-size:0.85rem'>ADB AI for Safer Roads 2026</span>"
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Data source ───────────────────────────────────────────────────────────
    st.markdown(
        "<p style='color:#94a3b8;font-size:0.75rem;text-transform:uppercase;"
        "letter-spacing:0.08em;margin-bottom:4px'>Data Source</p>",
        unsafe_allow_html=True,
    )
    data_source = st.radio(
        "data_source",
        ["Default ADB data", "Upload my own data"],
        label_visibility="collapsed",
    )

    df = None
    if data_source == "Upload my own data":
        st.markdown(
            "<small style='color:#64748b'>"
            "Formats: <b>CSV</b> · <b>GeoJSON</b><br>"
            "Required: <code>lat</code> <code>lon</code> <code>score</code> <code>diagnosis</code>"
            "</small>",
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader(
            "Drop file here",
            type=["csv", "geojson", "json"],
            label_visibility="collapsed",
        )
        if uploaded is not None:
            ext = uploaded.name.rsplit(".", 1)[-1].lower()
            with st.spinner("Parsing …"):
                df = load_csv(uploaded) if ext == "csv" else load_geojson(uploaded)
            st.success(f"{len(df):,} segments loaded")
        else:
            st.info("Showing default ADB data.")
            df = load_default()
    else:
        df = load_default()

    st.markdown("---")

    # ── Filters ───────────────────────────────────────────────────────────────
    cities = ["All regions"] + sorted(df["city"].dropna().unique().tolist())
    sel_city = st.selectbox("Region", cities)

    diag_labels = {
        "All diagnoses":              None,
        "Unsafe limit (red)":         "unsafe_limit",
        "Non-credible limit (amber)": "non_credible_limit",
        "Safe (green)":               "safe",
    }
    sel_diag = st.selectbox("Diagnosis filter", list(diag_labels.keys()))
    hp_only  = st.checkbox(f"High-priority only  (score ≥ {HIGH_PRIORITY_THRESHOLD})")

    st.markdown("---")

    # ── Map style ─────────────────────────────────────────────────────────────
    map_style = st.radio(
        "Map style",
        ["🌍 Light", "🌑 Dark"],
        horizontal=True,
        label_visibility="visible",
    )
    MAP_STYLE = "dark" if map_style == "🌑 Dark" else "road"

    st.markdown("---")

    # ── Legend ────────────────────────────────────────────────────────────────
    st.markdown("**Legend**")
    st.markdown(
        "🔴 **:red[Unsafe Limit]** — Reduce the limit  \n"
        "🟠 **:orange[Non-Credible]** — Redesign road  \n"
        "🟢 **:green[Safe]** — Speed aligned"
    )

# ── Filter ────────────────────────────────────────────────────────────────────
view_df = df.copy()
if sel_city != "All regions":
    view_df = view_df[view_df["city"] == sel_city]
if diag_labels[sel_diag]:
    view_df = view_df[view_df["diagnosis"] == diag_labels[sel_diag]]
if hp_only:
    view_df = view_df[view_df["high_priority"]]
view_df = view_df.dropna(subset=["lat", "lon"])

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown(
    "<div style='margin-bottom:16px'>"
    "<span style='font-size:0.75rem;color:#3b82f6;text-transform:uppercase;"
    "letter-spacing:0.1em;font-weight:600'>ADB AI for Safer Roads 2026</span>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Metrics ───────────────────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Segments shown",      f"{len(view_df):,}")
m2.metric("Unsafe Limit",        f"{(view_df['diagnosis']=='unsafe_limit').sum():,}")
m3.metric("High Priority",       f"{view_df['high_priority'].sum():,}")
m4.metric("Est. Lives Saved/yr", f"+{view_df['lives_saved_per_year'].sum():,.0f}")

st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

# ── Map ───────────────────────────────────────────────────────────────────────
if view_df.empty:
    st.warning("No segments match the current filters.")
else:
    centre_lat = view_df["lat"].mean()
    centre_lon = view_df["lon"].mean()
    zoom = 5 if sel_city == "All regions" else 7

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=view_df,
        get_position=["lon", "lat"],
        get_fill_color=["r", "g", "b", "a"],
        get_radius="radius",
        radius_min_pixels=1,
        radius_max_pixels=18,
        pickable=True,
        auto_highlight=True,
    )

    tooltip = {
        "html": (
            "<b style='color:#7dd3fc'>{segment_id}</b><br/>"
            "<span style='color:#94a3b8'>City:</span> {city}<br/>"
            "<span style='color:#94a3b8'>Diagnosis:</span> <b>{diagnosis}</b><br/>"
            "<span style='color:#94a3b8'>Score:</span> {score}<br/>"
            "<span style='color:#94a3b8'>S_safe:</span> {s_safe_str} &nbsp;"
            "<span style='color:#94a3b8'>Posted:</span> {posted_speed_str} &nbsp;"
            "<span style='color:#94a3b8'>P85:</span> {p85_speed_str}<br/>"
            "<span style='color:#94a3b8'>Rule:</span> {rule_str}<br/>"
            "<span style='color:#94a3b8'>Confidence:</span> {confidence}<br/>"
            "<span style='color:#94a3b8'>Lives/yr:</span> {lives_str}"
        ),
        "style": {
            "backgroundColor": "#0f1730",
            "color": "#e2e8f0",
            "fontSize": "13px",
            "padding": "12px 14px",
            "borderRadius": "8px",
            "border": "1px solid #1e3a5f",
            "boxShadow": "0 4px 20px rgba(0,0,0,0.5)",
        },
    }

    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=pdk.ViewState(
                latitude=centre_lat,
                longitude=centre_lon,
                zoom=zoom,
                pitch=0,
            ),
            tooltip=tooltip,
            map_style=MAP_STYLE,
        ),
        height=570,
    )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    "<p style='color:#334155;font-size:0.72rem;text-align:center'>"
    "SafeSpeed · Ventax AI Lab · Peshawar, Pakistan · "
    "WHO/GRSF Safe System thresholds · "
    "Nilsson–Elvik power model (e=4)"
    "</p>",
    unsafe_allow_html=True,
)
