# Aegis runtime image: API + explorer + CLI, with Chromium for browser agents.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[rag,mcp,otel]" && python -m playwright install --with-deps chromium
COPY tests ./tests
COPY evals ./evals
ENV AEGIS_DB=/data/aegis.db AEGIS_HEADLESS=1
VOLUME ["/data"]
EXPOSE 8080
CMD ["aegis", "serve", "--host", "0.0.0.0", "--port", "8080"]
