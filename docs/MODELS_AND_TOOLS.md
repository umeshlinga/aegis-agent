# Models and tools: what is used, where, and why

## LLMs

| Tier | Default model | Used for | Why this tier |
|---|---|---|---|
| fast | `claude-haiku-5-5` | planning, browser action selection, page picking, reflection, LLM rerank | Many small, latency-sensitive calls; $0.10 / $0.50 per M tokens (input/output, prompts ≤100K) |
| balanced | `claude-sonnet-5-5` | literature triage (×3 samples), fact extraction, outreach draft, vision coordinate fallback | Reading comprehension with exact quoting; $2 / $10 per M |
| strong | `claude-opus-5-5` | variant report, account brief | The text a human reads; $4 / $20 per M |
| judge_a | `claude-sonnet-5-5` | support verification | First judge |
| judge_b | OpenAI model you set, else local model, else judge_a | support verification | A **different model family** so the verifier is not grading its own model's homework |
| local (optional) | any Ollama/vLLM model, e.g. `qwen3:8b`, `llama3.1:8b` | fast-tier fallback, judge_b | Zero marginal cost, works offline, a real third provider for fallback tests |

Prices are list prices checked in November 2026 and live in `llm/base.py` (`DEFAULT_PRICING`); override with `AEGIS_PRICING_JSON`. Unknown models are reported as `unpriced_llm_calls` rather than silently counted as free.

**Escalation.** Structured calls that fail validation get two repair turns (the validation error is fed back), then move one tier up (fast → balanced → strong), then raise `SchemaViolation`. Escalations and fallbacks are recorded as events and show up in the failure taxonomy.

**Self-consistency.** Literature triage samples three answers at temperature 0.7 and keeps items seen in at least two. In the offline eval this alone removed every injected fake citation, before the verifier ran.

**Vision.** When every DOM-based click strategy fails, the browser screenshots the page and asks a vision-capable model for the target's coordinates, clicks there, and verifies the effect like any other click.

## Retrieval

| Piece | Implementation | Why |
|---|---|---|
| Chunking | 120–160 word windows, 30 word overlap | Abstract-sized passages keep quotes intact |
| Sparse | BM25 (own implementation, k1=1.5, b=0.75), tokenizer keeps `c.5095C>T`, `R1699W` intact | Variant notation is exact-token; dense embeddings blur it |
| Dense | `BAAI/bge-small-en-v1.5` via fastembed (ONNX, CPU) | Paraphrase ("abolished activity" vs "loss of function") |
| Fusion | Reciprocal Rank Fusion, k=60 | Combines rank lists without calibrating score scales |
| Rerank | cross-encoder `ms-marco-MiniLM-L-6-v2` (fastembed) or LLM listwise rerank | Precision at the top |
| Fallback | 512-d signed feature hashing | Dependency-free for tests; recorded as `hashing-512` in results so nobody mistakes it for the real embedder |

## Data sources (all real, all public)

| Source | Endpoint | Gives |
|---|---|---|
| Ensembl VEP REST | `rest.ensembl.org/vep/human/region` | consequence, MANE/canonical transcript, HGVS c./p., exon, REVEL, AlphaMissense |
| gnomAD GraphQL | `gnomad.broadinstitute.org/api` (dataset `gnomad_r4`) | AC, AN, AF, FAF95 popmax; gene LOEUF |
| ClinVar E-utilities | `esearch` + `esummary` (db=clinvar) | classification, review status, variation ID (comparison only) |
| PubMed E-utilities | `esearch` + `efetch` XML | abstracts |
| ClinVar web page | `ncbi.nlm.nih.gov/clinvar/variation/{id}` in real Chrome | the human-facing classification, for the browser cross-check |
| ClinVar variant_summary | NCBI FTP | benchmark construction with a temporal split |

## Engineering tools

| Tool | Where | Why |
|---|---|---|
| Python 3.11+, asyncio | everywhere | Concurrency for parallel agents and I/O |
| Pydantic v2 | tool inputs, every structured LLM output | Validation is the contract between model and code |
| httpx | HTTP client | async, timeouts, MockTransport for tests |
| SQLite (WAL) | durable state, cache, spans, memory | One file, crash-safe, zero ops; Postgres-ready schema |
| Playwright + Chrome/Chromium | browser agent | Real browser, tracing, locators |
| FastAPI + Uvicorn | API, explorer, `/metrics` | async, typed |
| MCP (Python SDK, FastMCP) | `aegis mcp` | Claude Desktop / Claude Code can call Aegis as tools; approval gate still applies |
| OpenTelemetry (optional) | tracer mirror | Same spans in Jaeger/Tempo |
| Prometheus | `/metrics`, compose service | Run counts by status, event counters, total LLM cost, pending approvals |
| numpy, scipy | metrics | Wilson intervals, exact McNemar, Mann-Whitney AUROC, Platt scaling, bootstrap |
| matplotlib | eval charts | Static PNGs embedded in the HTML report |
| pytest, pytest-asyncio | 52 tests | Unit, integration, real-browser |
| Docker, docker compose | packaging | API + Jaeger + Prometheus in one command |
| GitHub Actions | CI | Tests + offline eval quality gate (fails the build if Aegis success under faults < 0.95) |
