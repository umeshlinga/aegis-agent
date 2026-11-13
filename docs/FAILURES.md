# Failures: symptom → root cause → fix → measurement

Every failure becomes a test or an eval case. Entries F1–F6 were found while building Aegis (with AI pair-programming) and are reproducible from the repo. Live-run entries go in the second section as they happen, in the same format.

---

## Found during the build

### F1. Browser observe crashed during a late JavaScript navigation
* **Symptom:** company run failed in `browse`: `Page.evaluate: Execution context was destroyed, most likely because of a navigation`.
* **Root cause:** the "Products" link navigates 1.8s after the click. The recovery ladder's first rung (wait and re-verify) called `observe()` exactly while the new document was replacing the old one, so the injected JS lost its context. The ladder handled "nothing happened yet" but not "it is happening right now".
* **Fix:** `observe()` treats a destroyed context or navigation error as "page is in motion": wait for `domcontentloaded`, observe again (up to 4 tries), and only then raise `BrowserNoEffect` (retryable) instead of an unclassified error.
* **Measured:** the same run went from failed to succeeded with Products recovered by `wait_settle`; `test_browser_ladder_recovers_late_js_and_overlay` locks it in.

### F2. Retries could never outlast the circuit breaker
* **Symptom:** under the harsh fault profile one Aegis run in 60 failed with `CircuitOpen: circuit 'tool:gnomad' is open` after three step attempts.
* **Root cause:** two correct policies, designed separately. The tool retry capped its wait at `max_delay = 8s`; the breaker stayed open for `reset_timeout = 20s`. Once the breaker tripped, every retry arrived while it was still open and failed instantly, burning all attempts. The trace showed the pattern: `tool_retry {error_type: CircuitOpen, delay: 0.16}` four times in a row.
* **Fix:** `CircuitOpen` now carries `retry_after` = remaining open time; the retry policy honors any `retry_after` beyond `max_delay` (bounded at 60s). The breaker's own window drives the wait, so the first retry lands in the half-open probe.
* **Measured:** Aegis harsh-profile success 98.3% (59/60) → 100% (60/60) on the same seeded cases. Lesson: retry and breaker policies have to be designed together.

### F3. Cost and call totals disappeared after crash-resume
* **Symptom:** crash-profile runs reported ~2 LLM calls; uncrashed runs reported ~6 for the same work.
* **Root cause:** usage was accumulated in the in-memory `Budget`. The resumed run is a new process with a fresh budget, so everything spent before the crash vanished from the report. A cost dashboard built on that would under-report exactly the runs that are most expensive.
* **Fix:** usage is computed from persisted spans (`kind=llm`, `status=ok`), which survive the crash. Cached replays create no span, so they correctly cost nothing.
* **Measured:** crash-profile mean LLM calls 2.22 → 6.22, matching the no-crash 6.27 (the 0.05 gap is the work genuinely not redone). Test: `test_harsh_faults_recovered_and_crash_resumed` asserts totals survive the restart.

### F4. My grounding metric produced false positives
* **Symptom:** the eval said 44/60 Aegis reports contained ungrounded numbers, even though the report step's own check had passed.
* **Root cause:** the eval rebuilt the evidence blob without the run input, so the variant coordinate in every report (e.g. 100000149) looked invented. The bug was in the measurement, not in the system.
* **Fix:** the eval uses the same evidence blob shape the report step uses.
* **Measured:** 44 → 0 for Aegis; baseline (no grounding check) 60 → 16, which are the genuinely ungrounded "17 unrelated families" insertions. Lesson: validate the metric against hand-checked cases before trusting it.

### F5. Frameshifts were turned into missense notation
* **Symptom:** unit test: `protein_short("p.Gln1756ProfsTer74")` returned `Q1756P`.
* **Root cause:** the regex accepted the first three letters after the position as the new amino acid, so a frameshift looked like Gln→Pro missense. PubMed would have been searched for the wrong variant, silently.
* **Fix:** parse the remainder explicitly: `fs` → `Q1756Pfs`, `Ter`/`*` → nonsense, `=` → synonymous, otherwise exactly one 3-letter residue.
* **Measured:** test passes; added to the regression suite.

### F6. Eval chart was unreadable
* **Symptom:** five systems per fault profile, three in the same fallback color, legend over the title.
* **Fix:** the headline chart compares baseline vs Aegis only; ablations live in the table; legend moved under the title.

---

## Found in live runs (real APIs, real models)

Template, one entry per failure:

```
### L1. <short title>
* Run id(s):
* Symptom (what I saw, trace labels):
* Root cause (how I found it: span, event, payload):
* Fix (commit):
* Measured (metric before → after, same cases, n):
```

Things to watch for first, based on what each upstream is known to do: ClinVar `[chrpos38]` range queries for indels, VEP plugin fields (REVEL/AlphaMissense) missing for some transcripts, NCBI 429s without an API key, gnomAD GraphQL schema changes, PubMed searches with zero hits for HGVS c. notation, model JSON with trailing prose, ClinVar page layout changes breaking the cross-check regex (should fall back to LLM extraction), cookie banners and lazy-loaded menus on company sites.
