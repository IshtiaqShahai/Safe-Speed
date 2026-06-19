.PHONY: demo pipeline serve validate validate-phase4 test clean install

# ── Setup ─────────────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt

# ── Pipeline ──────────────────────────────────────────────────────────────────
demo:
	@echo "Generating sample data..."
	python data/sample/generate_sample.py
	@echo "Running full pipeline on Peshawar sample..."
	python -m core.pipeline --mode sample
	@echo "Done. Run 'make serve' to open the map."

pipeline:
	@echo "Running pipeline on ADB data in data/adb/..."
	python -m core.pipeline --mode adb

# ── Serve ─────────────────────────────────────────────────────────────────────
serve:
	@echo "Map UI → http://localhost:8501"
	streamlit run app.py

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	pytest tests/ -v

validate:
	pytest tests/ -v --cov=core --cov-report=term-missing

validate-phase4:
	@echo "Running Phase 4 validation analysis..."
	python notebooks/run_sensitivity.py
	@echo "Report: docs/validation_report.md"
	@echo "Raw results: docs/sensitivity_results.json"

# ── Docker ────────────────────────────────────────────────────────────────────
docker-up:
	docker compose up --build

docker-down:
	docker compose down

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache/ .coverage htmlcov/
