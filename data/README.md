# Data Directory

## data/sample/
Bundled open-data demo (Peshawar, Pakistan).  Ships with the repository.

| File | Description |
|---|---|
| `generate_sample.py` | Generates `peshawar_sample.geojson` with ~40 synthetic segments |
| `peshawar_sample.geojson` | Auto-generated on first `make demo` run |

Run: `python data/sample/generate_sample.py`

## data/adb/
ADB challenge datasets (gitignored — NDA applies).

Place the provided ADB files here.  The loader (`core/ingest.py`) expects:

| File pattern | Contents |
|---|---|
| `*.geojson` or `*.csv` | Road segments with speed, probe, and context attributes |

Required columns (others are optional):
- `segment_id` (or auto-generated)
- `posted_speed` / `speed_limit` / `maxspeed`
- `p85_speed` / `speed_85th`
- `road_class` / `highway`
- VRU context: `school_within_200m`, `market_within_200m`, etc.

Column aliases are handled automatically by `core/ingest.py::normalize_columns()`.

Run: `make pipeline` after placing files here.
