"""Quick upload test — run while server is running on port 8001."""
import httpx, time, sys
from pathlib import Path

BASE = "http://localhost:8001"

# Status check
r = httpx.get(f"{BASE}/api/upload/status")
print("Initial status:", r.json())

# Upload sample file
sample = Path(__file__).parent.parent / "data" / "sample" / "peshawar_sample.geojson"
with open(sample, "rb") as f:
    files = {"file": ("peshawar_sample.geojson", f, "application/json")}
    data  = {"city": "Peshawar", "country": "PK", "column_map": "{}"}
    r = httpx.post(f"{BASE}/api/upload", files=files, data=data, timeout=15)
    print("Upload response:", r.status_code, r.json())

# Poll until done
for _ in range(20):
    time.sleep(1.5)
    r = httpx.get(f"{BASE}/api/upload/status")
    j = r.json()
    status = j["status"]
    msg    = j["message"]
    print(f"  [{status}] {msg}")
    if status in ("done", "error"):
        break

print("\nFinal job state:", j)
