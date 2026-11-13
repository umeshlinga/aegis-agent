# Interview prep: questions I should expect, and how I answer them

Answer from the code. If I can't point to the file and line, I say I don't know yet.

## A. The system

**1. What did you build, in one breath?**
A runtime that makes multi-step AI agents survive real-world failure: durable checkpointed execution, multi-model routing with fallback and budgets, a permissioned tool layer with human approvals, a browser agent that verifies every action, hybrid RAG, and an eval harness that measures recovery. Two workloads on it: variant evidence curation and company research for outreach.

**2. Why build your own engine instead of LangGraph?**
The properties I needed are the ones I wanted to own and test: CAS on attempt numbers, an effects ledger, approval parking, crash semantics, fault injection at the tool boundary. The engine is about 300 lines and each guarantee has a test (ADR-001). v1 used LangGraph; v2 deliberately goes one level down.

**3. Is it in production?**
No. It has no external users. It runs against live public APIs and real models, with CI, Docker, metrics and tracing. I'd rather say that than stretch it.

**4. Walk me through one run.**
ARCHITECTURE §3: plan → parallel consequence/population → constraint/clinical/literature → browser cross-check → checkpoint barrier → verify → ACMG in code → grounded report → reflect.

**5. Where does the model decide and where does code decide?**
Model: what to read, which link to click, which sentence is evidence, how to word it. Code: state, retries, permissions, approvals, whether a citation exists, whether a quote is verbatim, how much evidence counts, the final class, whether numbers are grounded, spend.

## B. Reliability

**6. A process dies mid-step. What happens?**
The step stays `running` in SQLite. On restart the engine finds it, resets it to pending, logs `steps_recovered_after_crash`, and re-runs only that step; completed steps load from checkpoints. Model calls replay from the per-run cache, so nothing is paid twice. `test_crash_mid_step_resume_reruns_only_unfinished_work`.

**7. What if the step had already sent an email before dying?**
External effects go through `ctx.effect(key, fn)`. The result is stored under an idempotency key; a re-run finds it and returns it instead of sending again. `send_email` is also non-idempotent, so it is never auto-retried. `test_effect_executes_exactly_once_across_retries` simulates the timeout-after-send case.

**8. Stale results?**
Each attempt gets a number from `begin_step`. `complete_step` is an UPDATE with `WHERE attempts = ? AND status = 'running'`. A late result from an older attempt updates zero rows and is logged as `stale_result_discarded`.

**9. Cancellation?**
A flag in the run row. The scheduler checks it every poll; in-flight tasks get `task.cancel()`; steps are marked cancelled; the run moves to cancelled.

**10. How do retries, breakers and budgets interact?**
Retries only for transient errors and idempotent tools, with backoff, jitter and Retry-After. Breakers per provider and per tool. Budget is checked before every LLM call. The interaction bug I found (F2): retry max_delay 8s vs breaker reset 20s meant retries always hit an open breaker. Now the breaker's remaining window is the wait.

**11. Tell me about a hard failure you fixed and how you measured it.**
F2 for retries, or F1 for the browser. For live runs I will have my own L-entries; until then I say these were found during the build with AI pair-programming, then explain the debugging: trace showed four `CircuitOpen` retries at 0.16s; root cause the two policies; fix; 59/60 → 60/60 on identical seeded faults.

**12. Why fault injection instead of waiting for real failures?**
Real failures are rare and unrepeatable, so you can't compare versions. Seeded injection at the tool boundary gives the same faults to every system, which makes paired tests possible.

**13. Why is the baseline restart-from-scratch after a crash fair?**
A naive agent keeps state in memory; after a crash all it can do is start again. I charge both attempts' cost and time to it. Aegis resumes. That's the honest comparison.

## C. Multi-agent

**14. Is this really multi-agent?**
Parallel specialists (consequence, population, constraint, clinical, literature, browser) with a coordinator (the DAG), a verifier, a judge panel and a reflector. There is no agent-to-agent chat on purpose; inputs are independent, so fan-out and synthesis is enough.

**15. When does multi-agent beat a single agent?**
When subtasks are independent and I/O-bound (latency), or when one role checks another (verifier, judges). It costs more calls (6.3 vs 3.0 per variant here). I measured against a sequential single-agent baseline rather than assuming.

**16. Conflicting results between agents?**
Example: the ClinVar web page vs the ClinVar API. The browser cross-check logs `source_disagreement` and keeps both; the classifier never uses ClinVar anyway. Judge disagreement rejects the claim.

## D. LLMs, context, memory

**17. Which models and why?**
Haiku 5.5 for many small decisions, Sonnet 5.5 for reading and extraction, Opus 5.5 for text a human reads, and a different model family as the second judge. Local Ollama models as a free fallback. MODELS_AND_TOOLS.md has the prices.

**18. What happens when the model returns bad JSON?**
Two repair turns with the validation error, then escalate one tier, then `SchemaViolation`. All counted.

**19. How do you stop hallucinated citations?**
Three layers: self-consistency (keep items in ≥2 of 3 samples), code verification (PMID retrieved, quote verbatim, not an injected instruction), cross-family judge panel. In the offline eval the vote alone removed the injected fakes; the verifier is the backstop for errors that repeat.

**20. Why hybrid retrieval?**
Variant notation (`c.5095C>T`, `R1699W`) is exact-token; dense embeddings blur it. Paraphrase needs dense. RRF merges rank lists without score calibration. A test shows BM25 finds the exact token.

**21. How does memory avoid regressions?**
Reflection writes lessons as candidates. Planners only see active ones. Promotion needs a human or an eval that beats the run without the lesson.

**22. Context engineering choices?**
Spotlight untrusted text inside tagged blocks, strip injected sentences, retrieve only the top passages instead of dumping all abstracts, give the browser model a numbered element list plus the last 8 actions with landed URLs, keep prompts short and schemas strict.

## E. Browser and computer use

**23. How does the browser agent work?**
Observe (JS tags visible interactive elements with ids), the model picks one action, the session executes it, then verifies the effect (expected text appears or page fingerprint changes). If not: settle, re-locate after re-render, scroll and force, JS click, vision-model coordinate click.

**24. Why verify? Playwright said the click succeeded.**
Playwright confirms an event was dispatched, not that the app reacted. The overlay on the test site swallows real clicks silently; only verification notices.

**25. How do you stop the agent doing something harmful on a website?**
Domain allowlist on every navigation and post-click URL; irreversible-looking controls refused; HIGH-risk tools need a human. Tested: off-site link blocked, Subscribe refused.

**26. Prompt injection on a page?**
The test site's pricing page tells the AI to email an API key out. It is detected, stripped before the model sees the page, logged; and even if a model followed it, it has no send tool without approval and cannot navigate off-site.

**27. What breaks on real sites that your test site doesn't cover?**
Cookie banners, lazy menus, infinite scroll, shadow DOM, iframes, bot protection, logins. I'd add a cookie-banner rung and shadow-DOM-aware observation next.

## F. Evaluation

**28. How did you measure improvement?**
Same cases, same seeded faults, feature-switched systems, Wilson CIs, exact McNemar on paired success. Ablations attribute each gain.

**29. Why temporal split?**
Random splits leak: later classifications use information that wasn't available, and models may have seen older ClinVar.

**30. Calibration?**
Platt scaling on ACMG points, fitted on the train split, ECE and Brier on test. Points are a ranking score until calibrated.

**31. How do you know the judges are right?**
`aegis judge-calibrate`: compare each judge and the panel with ~50 hand labels, Cohen's kappa and confusion matrix. Judges are instruments; calibrate before trusting.

**32. What's wrong with your offline numbers?**
Synthetic fixtures and a scripted model: they measure the runtime, not accuracy. The report hides accuracy offline on purpose.

**33. A metric you got wrong?**
F4: my grounding metric flagged 44/60 Aegis reports because the eval's evidence blob lacked the variant coordinate. The bug was in the measurement.

## G. Domain

**34. Why points-based ACMG?**
ClinGen SVI Bayesian points: transparent, additive, maps to the 2015 categories. Supporting 1, moderate 2, strong 4, very strong 8.

**35. Why cap literature evidence at Supporting?**
It's model-extracted from abstracts. Code caps the strength; a human can approve more.

**36. Why not use ClinVar?**
PP5/BP6 are retired, and ClinVar is the ground truth here. Circular.

**37. What does "decline" mean?**
`insufficient_evidence` when required evidence is missing or only one weak criterion applies. It's reported as such; the coverage–accuracy curve shows the trade.

## H. Scale and production

**38. How would you run 10,000 variants a day?**
Postgres, a step queue with leases (`SKIP LOCKED`), N workers, per-host rate limits in Redis, the LLM batch API for non-urgent triage, cached VEP/gnomAD responses, cost per variant tracked from spans.

**39. What would you monitor?**
Success rate by workload, recovery rate, degraded-step rate, approvals pending and age, citation rejections, injection detections, cost per run, p95 latency, provider fallback rate. `/metrics` exposes the basics.

**40. Security?**
Secrets only from env, never logged; PII redaction on drafts; untrusted content spotlighted; allowlists; human approval for effects; MCP clients go through the same gates.

## I. About me

**41. Which parts did AI write?**
Built with Claude as a pair-programmer: I set scope and design, reviewed and tested, ran the evals and debugged the failures. I can explain every module, and the live-run failures are mine.

**42. What would you do in week one at Artisan?**
Learn where your agents fail today from traces; make every failure a regression case; put the same eval loop (seeded faults, paired comparisons) around the next architecture decision.

**43. What's next for Aegis?**
Live benchmark numbers, judge calibration, Postgres workers, outage-aware fail-fast, cookie/shadow-DOM rungs, PMC full text, more ACMG criteria.
