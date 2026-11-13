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

# Eval harness: run the full suite (recovery on/off ablation) and write
# the dated results table under evals/results/.
eval:
	$(PY) -m aegis.evals.run --suite evals/tasks.json

clean:
	rm -rf traces checkpoints .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
