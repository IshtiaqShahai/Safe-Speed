"""FastAPI application — serves scored segments and the interactive map UI.

Endpoints:
  GET  /                        → map UI (static HTML)
  GET  /api/segments            → all scored segments as JSON list
  GET  /api/segments/geojson    → GeoJSON FeatureCollection for MapLibre
  GET  /api/segments/{id}       → single segment detail
  GET  /api/summary             → network-level statistics
  POST /api/simulate            → Nilsson–Elvik lives-saved simulation
  POST /api/upload              → upload CSV/GeoJSON and run pipeline
  GET  /api/upload/status       → check pipeline job status
  GET  /api/upload/preview      → column preview of last uploaded file
  POST /api/pipeline/run        → trigger pipeline re-run (async)
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "docs"
STATIC_DIR = Path(__file__).parent / "static"
CONFIG_PATH = BASE_DIR / "core" / "config.yaml"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

# ── In-memory job state (single-user; replace with Redis for multi-user) ─────
_job: dict = {"status": "idle", "message": "", "file": "", "started": 0, "error": ""}

app = FastAPI(
    title="SafeSpeed",
    description="AI-assisted speed-limit audit for safer roads.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_scored_segments() -> list[dict]:
    """Load the most recent pipeline output from docs/."""
    candidates = [
        DOCS_DIR / "scored_segments.geojson",
        BASE_DIR / "data" / "sample" / "scored_segments.geojson",
    ]
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            features = data.get("features", [])
            return [f.get("properties", {}) for f in features]
    return []


def _load_geojson() -> dict:
    candidates = [
        DOCS_DIR / "scored_segments.geojson",
        BASE_DIR / "data" / "sample" / "scored_segments.geojson",
    ]
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return {"type": "FeatureCollection", "features": []}


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>SafeSpeed</h1><p>Run make demo first.</p>")


@app.get("/api/segments")
def list_segments(
    min_score: Optional[float] = None,
    diagnosis: Optional[str] = None,
    city: Optional[str] = None,
    confidence: Optional[str] = None,
):
    segments = _load_scored_segments()
    if min_score is not None:
        segments = [s for s in segments if (s.get("score") or 0) >= min_score]
    if diagnosis:
        segments = [s for s in segments if s.get("diagnosis") == diagnosis]
    if city:
        segments = [s for s in segments if s.get("city", "").lower() == city.lower()]
    if confidence:
        segments = [s for s in segments if s.get("confidence") == confidence]
    return {"count": len(segments), "segments": segments}


@app.get("/api/segments/geojson")
def segments_geojson():
    return _load_geojson()


@app.get("/api/segments/{segment_id}")
def get_segment(segment_id: str):
    segments = _load_scored_segments()
    for seg in segments:
        if str(seg.get("segment_id")) == segment_id:
            return seg
    raise HTTPException(status_code=404, detail=f"Segment {segment_id} not found")


@app.get("/api/summary")
def network_summary():
    summary_path = DOCS_DIR / "network_summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            return json.load(f)

    segments = _load_scored_segments()
    if not segments:
        return {"message": "No pipeline results found. Run 'make demo' first."}

    total = len(segments)
    diagnoses: dict[str, int] = {}
    for s in segments:
        d = str(s.get("diagnosis", "unknown"))
        diagnoses[d] = diagnoses.get(d, 0) + 1

    lives_total = sum((s.get("lives_saved_per_year") or 0) for s in segments)

    # Read threshold from network_summary.json if available (written by pipeline)
    ns_path = DOCS_DIR / "network_summary.json"
    hp_threshold = 70.0  # safe fallback
    if ns_path.exists():
        try:
            import json as _json
            with open(ns_path) as _f:
                hp_threshold = float(_json.load(_f).get("high_priority_threshold", 70.0))
        except Exception:
            pass

    return {
        "total_segments": total,
        "diagnosis_distribution": diagnoses,
        "unsafe_limit_pct": round(100 * diagnoses.get("unsafe_limit", 0) / total, 1),
        "high_priority_threshold": hp_threshold,
        "high_priority_segments": sum(1 for s in segments if (s.get("score") or 0) >= hp_threshold),
        "estimated_lives_saved_per_year": round(lives_total, 2),
    }


class SimulateRequest(BaseModel):
    segment_id: str
    speed_before: float
    speed_after: float
    annual_fatalities: float = 0.05
    intervention_class: str = "sign_plus_calming"


@app.post("/api/simulate")
def simulate_endpoint(req: SimulateRequest):
    cfg = _load_config()
    from core.simulator import simulate
    from core.models import SimulatorInput, InterventionClass

    inp = SimulatorInput(
        segment_id=req.segment_id,
        speed_before=req.speed_before,
        speed_after=req.speed_after,
        annual_fatalities=req.annual_fatalities,
        intervention_class=InterventionClass(req.intervention_class),
    )
    result = simulate(inp, cfg)
    return result.model_dump()


@app.post("/api/pipeline/run")
def trigger_pipeline(background_tasks: BackgroundTasks, mode: str = "sample"):
    def _run():
        import sys, subprocess
        subprocess.run(
            [sys.executable, "-m", "core.pipeline", "--mode", mode],
            cwd=str(BASE_DIR),
        )

    background_tasks.add_task(_run)
    return {"message": f"Pipeline triggered in background (mode={mode})."}


@app.get("/api/quality-report")
def quality_report():
    path = DOCS_DIR / "data_quality_report.json"
    if not path.exists():
        path = DOCS_DIR / "intermediate" / "data_quality_report.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"message": "Quality report not yet generated. Run the pipeline first."}


# ── Upload + pipeline ─────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".csv", ".geojson", ".json", ".parquet", ".xlsx"}
MAX_FILE_MB = 200


@app.post("/api/upload")
async def upload_dataset(
    file: UploadFile = File(...),
    city: str = Form("Peshawar"),
    country: str = Form("PK"),
    column_map: str = Form("{}"),   # JSON: {"your_col": "pipeline_col", ...}
):
    """Upload a dataset file and trigger the scoring pipeline."""
    global _job

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Save uploaded file
    save_path = UPLOAD_DIR / file.filename
    content = await file.read()

    if len(content) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_MB} MB limit.")

    with open(save_path, "wb") as f:
        f.write(content)

    # Parse custom column map
    try:
        extra_aliases = json.loads(column_map)
    except json.JSONDecodeError:
        extra_aliases = {}

    _job = {
        "status": "running",
        "message": "Pipeline started...",
        "file": file.filename,
        "started": time.time(),
        "error": "",
    }

    # Run pipeline in background thread
    def _run_pipeline():
        global _job
        try:
            _job["message"] = "Stage 1: Ingesting data..."
            import sys
            sys.path.insert(0, str(BASE_DIR))

            import yaml
            from core.ingest import load_adb_data, COLUMN_ALIASES
            from core.pipeline import run_pipeline, load_config

            cfg = load_config()
            cfg["_upload_city"] = city
            cfg["_upload_country"] = country

            # Apply custom column aliases for this run
            if extra_aliases:
                COLUMN_ALIASES.update(extra_aliases)

            _job["message"] = "Stage 1: Loading and normalising data..."
            df = load_adb_data(
                source=save_path,
                mode="file",
                output_dir=DOCS_DIR / "intermediate",
            )

            _job["message"] = "Stages 2–6: Scoring segments..."
            scored, report = run_pipeline(df, cfg, output_dir=DOCS_DIR)

            _job["status"] = "done"
            _job["message"] = (
                f"Complete. {report.total_segments} segments scored. "
                f"Unsafe limits: {report.total_segments - report.segments_with_posted_speed} flagged."
            )
            _job["result"] = {
                "total_segments": report.total_segments,
                "probe_coverage_pct": report.probe_coverage_pct,
                "high_confidence": report.high_confidence_count,
            }
        except Exception as exc:
            _job["status"] = "error"
            _job["error"] = str(exc)
            _job["message"] = f"Pipeline failed: {exc}"
            logger.exception("Pipeline failed after upload")

    thread = threading.Thread(target=_run_pipeline, daemon=True)
    thread.start()

    return {
        "message": f"File '{file.filename}' uploaded. Pipeline running.",
        "file": file.filename,
        "status_url": "/api/upload/status",
    }


@app.get("/api/upload/status")
def upload_status():
    """Poll this endpoint to check pipeline progress."""
    job = dict(_job)
    if job["status"] == "running" and job["started"]:
        job["elapsed_s"] = round(time.time() - job["started"], 1)
    return job


@app.get("/api/upload/preview")
def upload_preview(filename: str):
    """Return column names and sample rows from an uploaded file."""
    path = UPLOAD_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found in uploads/")

    try:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            import pandas as pd
            df = pd.read_csv(path, nrows=5)
        elif suffix in (".geojson", ".json"):
            from core.ingest import read_geojson
            import pandas as pd
            df = read_geojson(path).head(5)
        elif suffix == ".parquet":
            import pandas as pd
            df = pd.read_parquet(path).head(5)
        elif suffix in (".xlsx", ".xls"):
            import pandas as pd
            df = pd.read_excel(path, nrows=5)
        else:
            raise HTTPException(status_code=400, detail="Cannot preview this file type.")

        # Check which columns already match the pipeline
        from core.ingest import COLUMN_ALIASES
        pipeline_cols = {
            "posted_speed", "p85_speed", "aadt", "road_class", "is_divided",
            "has_footpath", "intersection_density", "length_m", "lat", "lon",
            "school_within_200m", "market_within_200m", "transit_stop_within_100m",
            "ptw_share", "probe_count", "segment_id", "road_name",
        }
        your_cols = list(df.columns)
        matched, aliased, unmatched = [], [], []

        for col in your_cols:
            col_l = col.lower()
            if col_l in {p.lower() for p in pipeline_cols}:
                matched.append(col)
            elif col_l in {a.lower(): t for a, t in COLUMN_ALIASES.items()}:
                aliased.append({"your": col, "maps_to": COLUMN_ALIASES.get(col_l, col_l)})
            else:
                unmatched.append(col)

        return {
            "filename": filename,
            "rows": len(pd.read_csv(path)) if suffix == ".csv" else "?",
            "columns": your_cols,
            "matched": matched,
            "aliased": aliased,
            "unmatched": unmatched,
            "sample": df.fillna("").head(3).to_dict(orient="records"),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
