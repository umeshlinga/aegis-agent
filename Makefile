.PHONY: install test eval serve doctor bench demo
install:            ## install with all extras + Chromium
	pip install -e ".[dev,rag,mcp,otel]" && python -m playwright install chromium
test:               ## unit + real-browser tests (offline, no keys)
	pytest -q
eval:               ## offline reliability eval (no keys needed)
	aegis eval --mode offline --n 60 --profiles none,harsh,crash_mid
eval-live:          ## live eval on the ClinVar temporal split (needs keys; costs money)
	aegis eval --mode record --cases evals/data/clinvar_benchmark.jsonl --n 40 --profiles none,harsh --calibrate --out evals/results/live
bench:              ## build ClinVar temporal-split benchmark
	aegis bench-build --cutoff 2024-06-30 --n-per-class 40
serve:              ## API + explorer on :8080
	aegis serve --port 8080
doctor:             ## check keys, browser and upstream APIs
	aegis doctor
demo:               ## the 4-minute demo, step by step (see docs/DEMO_SCRIPT.md)
	aegis variant 17-43045712-T-C --phenotype "hereditary breast and ovarian cancer"
