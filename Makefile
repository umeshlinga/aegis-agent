PY := .venv/bin/python

.PHONY: setup test smoke eval clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

# The smoke test is part of the suite; this target runs it on its own, verbosely.
smoke:
	$(PY) -m pytest tests/test_smoke.py -v

# Phase 2: eval harness entry point (task suite, recovery on/off ablation).
eval:
	$(PY) -m aegis.evals.run --suite evals/tasks.example.json

clean:
	rm -rf traces checkpoints .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
