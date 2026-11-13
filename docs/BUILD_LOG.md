# Build log: Aegis from scratch

What was built, in what order, with which tools, what went in, what came out, and what broke. Built on 13 November 2026 with Claude (Anthropic) as an AI pair-programmer; I directed scope and design, and I own understanding every line (see `INTERVIEW_PREP.md`). Live-API and live-model runs are done on my machine with my keys and logged in `FAILURES.md` and `EVALS.md` as they happen.

---

## Phase 0. Scope from the brief

**Input:** Jaspar Carmichael-Jack's email (Oct 25) asking for an agent system I personally built, whether it is in production, a tricky execution or recovery failure I fixed and how I measured the improvement, and any browser or computer-use automation. The Staff AI Engineer posting (execution loops, multi-agent coordination, concurrency/cancellation/stale results, checkpoints and recovery, human intervention, context and memory without regressions, evals on quality/reliability/latency/cost, model-vs-code guarantees, browser automation that verifies effects). My resume project, the genetic evidence agent (ClinVar temporal split, AUROC, declines unsupported variants). Kushal Vijay's AI engineering projects guide (15 blueprints).

**Decision:** one runtime that merges guide projects 3 (spend control / routing), 4 (guardrails), 5 (log-to-eval), 6 (permissioned tool agent), 11 (trace explorer), 12 (caching), 14 (evals/judges), with two workloads: my domain (variant evidence) and Artisan's domain (company research → outreach).

**Output:** the requirement-to-module map in `README.md`.

## Phase 1. Durable core

**Built:** `core/errors.py` (retryable vs permanent hierarchy; `SimulatedCrash` inherits `BaseException` so no `except Exception` can swallow it), `core/state.py` (run/step state machines with a transition table), `core/store.py` (SQLite WAL: runs, steps, events, approvals, spans, effects, llm_cache), `core/resilience.py` (retry with jitter and Retry-After, circuit breaker, loop guard, budget), `core/faults.py` (seeded fault profiles), `core/engine.py` (DAG scheduler).

**Key choices:** compare-and-set on attempt number for step completion (stale results); effects ledger keyed by idempotency key (exactly-once sends); interrupted `running` steps reset on resume; optional steps degrade instead of failing the run.

**Commands:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install pydantic httpx anthropic openai fastapi uvicorn playwright python-dotenv numpy scipy pytest pytest-asyncio
```

## Phase 2. Multi-model layer

**Built:** `llm/base.py` (provider-neutral request/response, pricing table), `llm/providers.py` (Anthropic, OpenAI, OpenAI-compatible local; record/replay; offline stand-in), `llm/router.py` (tiers, fallback chain, per-provider breakers, budget pre-check, per-run cache, structured output with repair and escalation, self-consistency vote, cross-family judge panel, tolerant JSON extraction). Vision support added later for the browser fallback.

**Key choices:** SDK retries disabled (`max_retries=0`) so retries happen once, in the router, traced and budgeted. Cache scoped per run: resume is free and deterministic, but separate eval runs never share answers. Model IDs and prices verified against current Anthropic pricing (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 per M tokens).

## Phase 3. Tools, guardrails, tracing

**Built:** `tools/registry.py` (roles, Pydantic args, LOW/MEDIUM/HIGH risk, approval gate, idempotent-only retries, breakers, fault hook, injection scan of untrusted output, spans), `tools/http.py` (shared client, per-host rate limits, error mapping), `tools/bio.py` (VEP region encoding for SNVs/indels, gnomAD GraphQL incl. "not found" = absent, ClinVar esummary with SPDI matching and the 2024 `germline_classification` schema, PubMed XML, gnomAD gene constraint), `guardrails/injection.py` (pattern detection, sentence stripping, spotlighting), `guardrails/pii.py`, `tracing/tracer.py` (contextvar span nesting, SQLite + optional OTLP), `tracing/taxonomy.py` (rule-based failure labels).

## Phase 4. Browser automation

**Built:** `tools/browser.py` (observe via injected JS that tags visible interactive elements; act; verify by expected text or page fingerprint; recovery ladder: settle → re-locate after re-render → scroll+force → JS click → vision coordinate click; domain allowlist; irreversible-control refusal; Playwright tracing), `agents/browser_agent.py` (LLM picks one action per step from a structured schema, injection-cleaned spotlighted page text, loop guard, history with landed URLs).

**Test site:** `tests/site/` is a small company website built to break agents: a "Products" link that navigates 1.8s after the click (late JS), a transparent overlay on "Pricing" that swallows real clicks, a paragraph instructing the AI to send an API key to an outside address, an off-site "Partner portal" link, and a "Subscribe" button.

**First real run** (local site, real Chrome):
```bash
python -m http.server 8765 --directory tests/site &
python /tmp/co.py      # company workload, offline model, real browser
```
Result: crashed with `Execution context was destroyed`. Root cause and fix in `FAILURES.md` (F1). After the fix: 6 pages captured, Products recovered by `wait_settle`, Pricing recovered by `js_click`, injection detected on the pricing page and stripped, run parked at the `send_email` approval, approved, resumed, sent exactly once.

## Phase 5. Retrieval and memory

**Built:** `rag/index.py` (own BM25, dense embedder chain fastembed → OpenAI → hashing, RRF, cross-encoder or LLM rerank, filters, SQLite persistence), `rag/memory.py` (reflection writes candidate lessons; only promoted lessons are recalled).

## Phase 6. Workloads

**Variant:** `workloads/variant/acmg.py` (ClinGen points system, BA1/BS1/PM2_Supporting/PVS1 tree/PP3-BP4 with Pejaver 2022 REVEL thresholds/PS3-BS3 capped; decline rule), `workload.py` (13-step DAG, see ARCHITECTURE §3), `fixtures.py` (synthetic, clearly fake genes/PMIDs).

**Company:** `workloads/company/workload.py` (browse → extract → verify → brief → outreach with HIGH-risk send).

**Smoke tests:**
```bash
python /tmp/smoke.py          # 5 offline variant runs: all succeeded
python /tmp/smoke.py harsh    # same under 32% upstream failure: all succeeded
python /tmp/crash.py          # crash during verify -> new runtime resumes -> succeeded, consequence ran once
```

## Phase 7. Evaluation harness

**Built:** `evals/harness.py` (systems are feature sets over the same code; baseline = sequential, no retries, no degradation, no verifier, no judges, no grounding, single sample, no abstention; ablations remove one feature each; ClinVar masked; crash handling resumes Aegis and restarts the baseline from scratch, charging both attempts), `evals/metrics.py` (Wilson CI, exact McNemar, AUROC, bootstrap CI, Platt scaling fit on train only, ECE, Brier, coverage–accuracy), `evals/report.py` (charts + HTML), `evals/build_clinvar.py` (temporal split from variant_summary), `evals/judge_calibration.py` (Cohen's kappa vs human labels).

**First eval** exposed three more bugs (F2, F3, F4 in `FAILURES.md`): the breaker/retry mismatch, usage totals lost across a crash-resume, and a false positive in my own grounding metric.

```bash
aegis eval --mode offline --n 60 --systems baseline,aegis,no_verifier,no_retries,no_degrade \
  --profiles none,mild,harsh,outage,crash_mid --out docs/results/offline-2026-11-13
```
Output: `docs/results/offline-2026-11-13/report.html` (table in README).

## Phase 8. Interfaces

**Built:** `cli.py` (variant, company, resume, cancel, runs, show, approvals, approve, eval, bench-build, judge-calibrate, export-failures, memory, serve, mcp, doctor), `api/server.py` (REST, background runs, startup recovery of interrupted runs, approvals that auto-resume, `/metrics`), `api/explorer.html` (runs, approvals with approve/reject, stats, failure-label chips, span waterfall, steps, events, result), `mcp_server.py` (curate_variant, research_company, get_run, pending_approvals, decide_approval).

Verified over HTTP: started a variant and a company run with `curl`, watched the company run park in `waiting_approval`, approved via `POST /api/approvals/1`, run resumed to `succeeded`; `/metrics` reported runs by status and event counters.

## Phase 9. Tests, packaging, CI

52 tests (`pytest -q`, ~55s): engine guarantees (parallelism, retry, permanent errors, degradation, skip conditions, approvals, cancel, crash mid-step, stale CAS, exactly-once effect), resilience and router (Retry-After, breaker half-open, loop guard, budget, fallback, repair→escalate→fail, per-run cache, voting, JSON extraction, no fallback on permanent errors), domain (ACMG rules, injection, PII, VEP region encoding, protein notation, mocked ClinVar/gnomAD/PubMed parsing, HTTP error mapping, BM25 vs exact tokens, hybrid filters, memory promotion), end to end (offline runs, hallucinated citations rejected, ungrounded numbers repaired, harsh faults and crash recovery, eval harness), real Chrome (ladder recovers late JS and overlay, no-ladder fails, off-site and risky clicks blocked, full company run with approval and exactly-once).

A test caught F5 (frameshift protein notation).

Packaging: `pyproject.toml` (extras: rag, mcp, otel, dev), `Dockerfile`, `docker-compose.yml` (Aegis + Jaeger + Prometheus), `Makefile`, `.github/workflows/ci.yml` (tests + offline eval gate at 0.95).

## Phase 10. From demo to tool I use

**Built:** `batch.py` (VCF/VCF.gz/CSV/TSV parsing with multi-allelic splitting, symbolic/filtered/chrM handling; deterministic run ids so re-running a batch resumes; shared rate limits; `results.tsv`, per-variant reports, batch index page), `reports/variant_report.py` (printable report: classification, criteria with points and sources, raw evidence, verified quotes with PubMed links, rejected evidence with reasons, ClinVar comparison, provenance), `watch.py` (watchlist that re-curates and diffs label, points, criteria, citations, new papers and ClinVar; change history; markdown digest), `workloads/llm_only` + `evals/compare.py` (the strongest model asked directly vs Aegis, every cited PMID looked up in PubMed, scored against ClinVar), `api/app.html` (product web app: curate, batch upload, watchlist, comparison), new endpoints, CLI `batch`, `report`, `watch`, `compare`.

```bash
aegis batch examples/demo_offline.vcf --mode offline        # 11 variants; 3 skipped with reasons
aegis batch examples/demo_offline.vcf --mode offline        # again: "already_done": 11
aegis compare --mode offline --n 30                         # LLM-alone vs Aegis report
aegis serve                                                 # product app on :8080, ops explorer on /explorer
```
52 tests pass. Market research written up in `MARKET.md` (honest conclusion: grounded variant agents are an active field; Aegis's angle is measured reliability, reanalysis, and hard verification).

## Phase 11 (next, on my machine with keys)

1. `aegis doctor` until every line is OK.
2. `aegis variant 17-43045712-T-C` and nine more variants I know; log every failure in `FAILURES.md` with before/after.
3. `make bench` (temporal split, cutoff 2024-06-30), `make eval-live` with `--mode record` so reruns replay for free.
4. `aegis judge-calibrate` on 50 statements I label by hand; report kappa.
5. `aegis company <real site> --outreach` on 10 public company sites; log browser failures and recoveries.
6. Record the 4-minute demo (`DEMO_SCRIPT.md`).
