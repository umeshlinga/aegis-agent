PY := .venv/bin/python

.PHONY: setup test smoke eval demo explorer clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

# The smoke test is part of the suite; this target runs it on its own, verbosely.
smoke:
	$(PY) -m pytest tests/test_smoke.py -v

# Eval harness: run the full suite (recovery on/off ablation) and write
# the dated results table under evals/results/.
eval:
	$(PY) -m aegis.evals.run --suite evals/tasks.json

# Walkthrough demo: three scored showcase tasks against the local test
# site, narrated from their traces. Presenter script: demo/WALKTHROUGH.md
demo:
	$(PY) -m aegis.demo

# Trace explorer: read-only web UI over the demo trace set (generated on
# first run). Serves http://127.0.0.1:8765/
explorer:
	$(PY) -m aegis.explorer

clean:
	rm -rf traces checkpoints .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
