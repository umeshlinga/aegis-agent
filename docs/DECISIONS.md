# Architecture decision records

**ADR-001: No agent framework in the core.** LangGraph would give a graph and a checkpointer, but the properties I needed are exactly the ones I wanted to own and test: attempt-number CAS, an effects ledger, approval parking, crash semantics, fault injection at the tool boundary. The engine is ~300 lines and every guarantee has a test. Trade-off: no ecosystem integrations for free. My v1 used LangChain/LangGraph; v2 is a deliberate step down the stack.

**ADR-002: SQLite (WAL) now, Postgres when there are multiple workers.** One process, one file, crash-safe, zero ops, inspectable with `sqlite3`. The schema is plain SQL. Moving to Postgres means a driver swap plus a work queue using `SELECT ... FOR UPDATE SKIP LOCKED` on pending steps, and per-step leases (heartbeat + expiry) instead of "a RUNNING step at startup belongs to a dead process".

**ADR-003: The model never assigns the final class.** ACMG combining is deterministic Python; the LLM only proposes literature evidence, which is verified and capped at Supporting. Same evidence → same answer, and every point traces to a source. Cost: less nuance than an expert curator; that is the right failure direction for a clinical-adjacent tool.

**ADR-004: ClinVar is never evidence.** ClinGen retired PP5/BP6, and ClinVar is the benchmark label. Using it as input would be circular and would inflate accuracy. It is fetched for display and for the browser cross-check, and masked during evals.

**ADR-005: Temporal split, not random.** Variants evaluated after the cutoff were classified with information the system and the models might not have had in training. A random split leaks.

**ADR-006: Retries live in one place.** SDK retries off; the router and tool executor retry, with traces, budget checks and fault injection on the same path. Two layers of hidden retries multiply latency and hide failures.

**ADR-007: Retry only idempotent tools.** `send_email` is non-idempotent: no automatic retry, exactly-once via the effects ledger, HIGH risk via approval. Uncertain outcomes (timeout after the send) are handled by the ledger, not by guessing.

**ADR-008: Cross-family judges.** A judge from the same model family as the generator shares its blind spots. The panel requires agreement between judge_a (Claude) and judge_b (OpenAI or a local model). Judges are calibrated against human labels (Cohen's kappa) before they are trusted.

**ADR-009: Self-consistency on the highest-leverage call only.** Three samples on literature triage (it creates evidence), not on every call. Cost roughly doubles LLM calls per run (3.0 → 6.3 in the offline eval); worth it there, not elsewhere.

**ADR-010: Browser actions are verified, not assumed.** "Clicked successfully" from Playwright means an event was dispatched, not that anything happened. Every action has a postcondition (expected text or page fingerprint change) and a ladder of increasingly forceful recoveries, ending with a vision model. The no-ladder ablation fails on the flaky test site.

**ADR-011: Injection defense is permissions, not detection.** Detection and stripping reduce exposure; the guarantee is that the browser cannot leave the allowlist, cannot click irreversible controls, and that external effects need a human.

**ADR-012: Memory with a promotion gate.** Lessons from reflection start as candidates and are not recalled until promoted (by a human or by an eval showing improvement). Feedback cannot silently cause regressions.

**ADR-013: Per-run LLM cache.** Resume replays the run's own model outputs (deterministic, free); runs never share answers (eval costs stay honest). A global semantic cache is a separate, opt-in decision with leakage risks.

**ADR-014: Multi-agent only where it pays.** Specialists run in parallel because their inputs are independent I/O-bound calls; there is no agent-to-agent chat. The eval compares against a sequential single-agent baseline so the claim "parallel specialists help" is measured, not assumed.
