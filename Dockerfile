FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY evals ./evals

RUN pip install --no-cache-dir -e ".[dev]"

# Phase 1 default: run the test suite (which includes the end-to-end smoke test).
CMD ["python", "-m", "pytest", "-q"]
