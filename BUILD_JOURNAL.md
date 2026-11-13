# Build Journal

I am keeping this journal as I build Aegis, in my own words, one entry per
work session. The point is simple: if someone asks me in a room how this
was built, what I chose, and what I rejected, I want the honest record to
exist, written at the time, not reconstructed later.

## Day 1: scaffold, core loop, and the rules the system lives by

### What I am building and why

Aegis is a runtime for tool-using agents. Not a demo script that calls a
model once, but the layer you would actually need if an agent had to do
long, real work on a computer: a way to plan a task, act on it step by
step, check each step, recover when something breaks in the middle, ask a
human before doing anything that cannot be undone, and keep a complete
record of what happened.

I have run agents against real web interfaces before, and the failures
were never the interesting part. The interesting part was what the system
did next. A script dies. A runtime should notice the failure, name it,
and either fix its approach or stop and say why. That difference is the
whole project.

The design follows the permissioned agent sandbox pattern: a planner, a
tool registry with permission scopes, a recovery loop, and full tracing.
I scaled it in two places where my own experience says the value is: a
multi-model router, because different steps deserve different models, and
an eval harness with recovery switched on and off, because I do not want
to claim recovery helps. I want to measure it.

### Decisions I made today, and what I considered instead

**State machine first, graph library second.** The agent core is a state
machine with six states: plan, act, verify, recover, done, failed. I
considered starting directly on a graph framework, and I will likely bind
one later, but I wanted the states and the transitions to be mine and to
be visible in the trace from the first day. If the core loop is wrong, no
framework saves it. The runner interface is shaped so a graph library can
replace the internals without changing the contract.

**Permissions live in the registry, not in prompts.** Every tool declares
a scope: read, write, act, or spend, plus whether it is irreversible.
Anything irreversible needs a human approval decision before it runs, and
if no approval path is configured, the tool is blocked. I considered
putting the safety rules in the system prompt, the way a lot of agent
demos do. I rejected that. A prompt is a suggestion to a model. A registry
check is code that runs every time. If a model is jailbroken, confused,
or just wrong, the gate still holds. The recovery engine also treats a
permission denial as final. The agent does not get to argue with the gate
or route around it.

**A fake model as a first-class citizen.** The system runs end to end
with a scripted fake model adapter that needs no API key and no network.
This was a deliberate choice, not a shortcut. Tests and CI cannot depend
on a provider being up, a key being valid, or a model answering the same
way twice. With the fake, the full loop is deterministic and testable
today, and real providers slot in behind the same adapter interface when
I connect them. The trace marks fake runs with the model name
`fake-scripted`, so a test run can never be mistaken for a real result.

**Recovery as a taxonomy, not a retry counter.** When a step fails, the
failure gets classified: element not found, stale state, tool error,
model refusal, timeout, permission denied, verify failed. The retry, when
there is one, carries a changed strategy that matches the kind. Backoff
for a transient tool error. A wider search for a missing element. A state
re-read for stale state. And a rollback to the last verified checkpoint
when retries are spent on a state-shaped failure. I have seen too many
agents retry the identical call three times and call that resilience. It
is not. The diagnosis goes into the trace, so I can read later why the
run did what it did.

**Traces are the only results.** Every step, tool call, model call, and
recovery event is one JSON line in a trace file. The eval harness will
score runs from traces, and any number I publish about this project will
be computed from traces by the harness. Not typed from memory, not
estimated. If it is not in a trace, it did not happen.

**One model per step kind, chosen by a router.** Planning, verifying,
and diagnosing are different kinds of work. The router picks a model per
step kind, walks a fallback chain if a provider errors, and logs tokens,
latency, and cost for every call. Fallbacks are traced too. If a run
quietly finished on a weaker model, I want the record to say so.

### Tools I chose today

- Python 3.12, standard library only for Phase 1. No framework lock-in
  before the design has earned it.
- pytest for tests, including the end-to-end smoke test.
- JSONL files for traces and JSON files for checkpoints. Boring, readable
  with any tool, easy to inspect by eye. A database can come later if the
  trace explorer needs one.
- Docker Compose skeleton, so the stack has a home from day one even
  though Phase 1 is a single local process.

### What Phase 1 produced

- Repo scaffold: src-layout package, pyproject, Docker Compose skeleton,
  Makefile, evals directory.
- The agent core: state machine, checkpoint store, plan-act-verify-recover
  loop.
- The tool registry with permission scopes, run policies, and the
  approval gate, plus stub tools over an in-memory note store and a
  spend-scope charge stub that exists to exercise the gate.
- The trace writer and reader.
- The model router with the scripted fake adapter.
- The recovery engine with the taxonomy, diagnosis, retry with a changed
  strategy, rollback decision, and the ablation switch.
- The eval suite loader, with an example suite of four tasks (two API,
  two browser).
- Eight tests, all passing: the two-step smoke test end to end, the
  approval gate blocked and granted, transient failure recovering, the
  same failure aborting with recovery off, and trace read-back.

What is not built yet, honestly: the real provider adapters, the browser
tool, the eval execution and scoring, and the trace explorer UI. Those
are Phase 2 and 3, in that order.


## Day 2: real adapters, the browser arm, and the first measured ablation

### What I built today

**Real model adapters.** Anthropic, OpenAI, and a local Ollama-style
adapter now sit behind the Phase 1 adapter interface. Three decisions
worth recording. First, keys come only from the environment (or a .env
file that never overrides the real environment), and nothing anywhere
prints a key; `configured_providers()` reports booleans, not values.
With no key set, an adapter raises a typed MissingAPIKeyError before any
network call, and because that is a ModelError, the router just falls
back to the next adapter in the chain. A deployment with one key routes
cleanly. Second, the adapters use the standard library HTTP client, not
the provider SDKs. The wire formats for a chat completion are small and
stable, and zero new dependencies means the local mode stays zero
dependency. An SDK can replace the transport later without touching the
interface; the transport is an injectable callable precisely so tests
stub it and never touch the network. Third, cost: providers report
tokens, not dollars, so each adapter computes cost from a small local
price table and sets cost_is_estimate on the response, and the router
writes that flag into the trace. An estimate can never be mistaken for a
billed figure. Honest limit: there are no provider keys in this
environment, so the adapters are verified against stub transports and by
their clean failure without keys. No live-API verification happened, and
I am not claiming any.

**The browser arm, on Playwright.** The question of the day was which
driver to ship. Playwright's Python package was not installed, but its
Chromium builds were already in the local browser cache, and installing
the package plus the matching headless shell worked. So Phase 2 ships
the real thing: Chromium rendering real pages, including their
JavaScript, driven through Playwright. The DOM-level fallback driver I
had scoped as the alternative was not needed. Grounding is semantic:
get_by_role with role and accessible name, get_by_label for form fields,
never a CSS id. Every action verifies: a click must change the page or
reach the expected text the caller declared, a fill must read back the
value it set. Failures are typed (element_not_found, stale_state,
timeout, tool_error, verify_failed) and the exception's kind attribute is
what the registry hands to the recovery engine.

One design point I want on the record: the recovery engine names a
strategy (re-ground, re-read state, extend the wait), but a retry is the
same tool call with the same arguments, so the strategy change has to
live in the tool arm. It does. After an element_not_found, the next
attempt at that target waits patiently instead of probing briefly.
After a stale_state, the next attempt at that handle re-grounds from the
handle's stored descriptor. After a timeout, the next wait for that text
runs longer. The engine decides whether a retry happens at all; the arm
makes the retry different from the attempt that failed.

**A test site designed to break agents.** A stdlib HTTP server hosts
pages with specific hostile behaviors, all deterministic: a button that
renders 1.2 seconds after load, a button whose label changes after 0.9
seconds, a form whose first submit per session is rejected with a
transient error and whose second succeeds, a list that rebuilds itself
in rotated order every 0.9 seconds so captured handles genuinely detach,
a report endpoint that answers 503 exactly once per session, a data
endpoint that takes 2.5 seconds exactly once per session, and a delete
workspace flow behind a confirmation page. State is per session, and the
harness reads a session's final state straight from the server object to
score runs.

**Eval execution and scoring.** aegis.evals.run is real now. Each task
carries a fixed plan in its setup, returned by the scripted fake model,
and a mechanical scorer: run status, note-store contents, charge ledger
length, server-side session state, and trace assertions (event types and
payload fields, present and forbidden). Each task runs twice, recovery
on and recovery off. The suite grew to 30 tasks: 16 browser, 14 API,
including spend actions that must be refused without approval, an
irreversible browser action that must be refused, tasks whose correct
outcome is an honest failure, and 13 tasks with a designed-in mid-task
failure. Tests went from 8 to 28, all passing.

### Results, pasted verbatim from the harness output

# Aegis eval results: aegis-suite-v2

Suite version: aegis-suite-v2 | Date: 2026-11-13 | Model: fake-scripted | Browser driver: Playwright (Chromium headless)

Each task ran twice against the full runtime: recovery enabled and recovery disabled (ablation). Scoring is from final state plus trace assertions. The planner is the scripted fake model with a fixed plan per task, so these numbers measure the runtime: permission gates, the browser tool, and recovery.

| Mode | Tasks run | Passed | Pass rate | Mean latency per run (s) | Recovery events per passed task |
| --- | --- | --- | --- | --- | --- |
| recovery on | 30 | 30 | 100.0% | 0.86 | 0.93 |
| recovery off | 30 | 17 | 56.7% | 0.50 | 0.35 |

## Per task

| Task | Kind | Recovery on | Recovery off |
| --- | --- | --- | --- |
| b01-late-button | browser | pass | FAIL |
| b02-renamed-button | browser | pass | FAIL |
| b03-form-transient-rejection | browser | pass | FAIL |
| b04-stale-handle-list | browser | pass | FAIL |
| b05-flaky-report | browser | pass | FAIL |
| b06-slow-report | browser | pass | FAIL |
| b07-delete-refused-no-approval | browser | pass | pass |
| b08-delete-approved | browser | pass | pass |
| b09-delete-approval-declined | browser | pass | pass |
| b10-shop-order | browser | pass | pass |
| b11-clean-form | browser | pass | pass |
| b12-home-to-form | browser | pass | pass |
| b13-list-click-by-label | browser | pass | pass |
| b14-late-then-rename-chain | browser | pass | FAIL |
| b15-form-transient-second-person | browser | pass | FAIL |
| b16-slow-report-tight-budget | browser | pass | FAIL |
| a01-read-inbox | api | pass | pass |
| a02-append-line | api | pass | pass |
| a03-append-new-note-twice | api | pass | pass |
| a04-charge-refused-no-approval | api | pass | pass |
| a05-charge-approved | api | pass | pass |
| a06-charge-approval-declined | api | pass | pass |
| a07-flaky-append-once | api | pass | FAIL |
| a08-flaky-append-twice | api | pass | FAIL |
| a09-flaky-read-then-append | api | pass | FAIL |
| a10-missing-note-honest-failure | api | pass | pass |
| a11-seeded-multi-note | api | pass | pass |
| a12-charge-then-note | api | pass | pass |
| a13-flaky-charge-approved | api | pass | FAIL |
| a14-impossible-expectation | api | pass | pass |

## Failure detail

- b01-late-button (recovery off): run_status is 'failed', want 'done'
- b02-renamed-button (recovery off): run_status is 'failed', want 'done'
- b03-form-transient-rejection (recovery off): run_status is 'failed', want 'done'; server_state 'form_submission.email' is None, want 'ada@example.test'; server_state 'form_submission.fullname' is None, want 'Ada Lovelace'
- b04-stale-handle-list (recovery off): run_status is 'failed', want 'done'; server_state 'viewed_item' is None, want 'Bravo'
- b05-flaky-report (recovery off): run_status is 'failed', want 'done'; server_state 'flaky_hits' is 1, want 2
- b06-slow-report (recovery off): run_status is 'failed', want 'done'
- b14-late-then-rename-chain (recovery off): run_status is 'failed', want 'done'
- b15-form-transient-second-person (recovery off): run_status is 'failed', want 'done'; server_state 'form_submission.email' is None, want 'alan@example.test'
- b16-slow-report-tight-budget (recovery off): run_status is 'failed', want 'done'
- a07-flaky-append-once (recovery off): run_status is 'failed', want 'done'; note 'inbox' does not contain 'recovered line'
- a08-flaky-append-twice (recovery off): run_status is 'failed', want 'done'; note 'inbox' does not contain 'twice recovered'
- a09-flaky-read-then-append (recovery off): run_status is 'failed', want 'done'; note 'inbox' does not contain 'after flaky read'
- a13-flaky-charge-approved (recovery off): run_status is 'failed', want 'done'; ledger holds 0 entries, want 1

### What these numbers do and do not prove

The honest read: on this suite, recovery is the difference between 17
and 30 tasks passing, and the split is exactly where it was designed to
be. Every task with a designed-in failure passes with recovery on and
fails with it off; every benign and safety task passes in both modes.
That is real evidence that the taxonomy, the strategy-changing retries,
and the gates work as built, measured end to end through the registry,
a real browser, and a real server.

What it does not prove: anything about a real model's planning. The
planner here is scripted, with a fixed plan per task, so no number in
that table says the system would plan its way through an unfamiliar
task. The tasks are local and small, the hostile behaviors are mine
(chosen to exercise the taxonomy, so the suite is partly a test of
whether the system survives my own traps), and timings are one
machine's. The adapters are unverified against live providers. The next
honest step is Phase 3 work plus a run with a real model in the planner
seat and tasks I did not design around the recovery taxonomy.


## Day 3: the demo, the trace explorer, the case study, and publication

### What I built today

**The walkthrough demo.** `python -m aegis.demo` runs three beats
against the local hostile test site, narrated live from the traces the
runs themselves write. Beat 1 is the renamed button: the click fails as
element_not_found, recovery diagnoses it, and the retry re-grounds and
completes the task. Beat 2 is the transiently rejected form: the submit
click fails verification, recovery classifies it, and the retry lands
the submission, proven against the server's own records. Beat 3 is the
permission gate, twice: a 25 dollar charge with no approval function
configured is refused before the tool runs and the ledger stays empty,
then the same charge with an approval function that grants it
completes. One deliberate choice: the demo runs on the eval harness's
own runner, so every beat is scored by the same mechanical scorers as
the suite. A demo that grades itself with its own ruler is worth more
than a scripted success. demo/WALKTHROUGH.md is the presenter script:
what to run, what the audience sees at each beat, and the one-line
explanation I would give out loud.

**The trace explorer.** A read-only web UI over the JSONL trace store,
standard library only, one embedded page, no build step
(`python -m aegis.explorer`). It lists runs with task, mode, outcome,
and duration, and opens each run as an ordered timeline: state
transitions, model calls with tokens, latency, and cost, tool calls
badged with their permission scope, verification results, and recovery
events, with recovery highlighted in amber and permission-gate refusals
in red. Two decisions worth recording. First, the explorer opens on a
generated demo set: eight tasks from the suite (including a
designed-failure browser task and the refused spend task) run in both
modes through the real eval runner, so the timelines are real runs, not
fixtures. Second, run modes live in a small manifest next to the trace
file, written by the generator, because the runtime's trace format does
not record which ablation mode produced a run; rather than guess from
the events, the explorer reports "not recorded" for trace files that
have no manifest. Permission scopes are looked up from the actual tool
factories, so the badges cannot drift from the registry's declarations.
The server refuses every non-GET method; it cannot mutate anything.

**The case study.** The README now carries the case study end to end:
the problem, the design, the eval method, the v2 results tables copied
verbatim from the harness output file (I checked them byte for byte
against the results file, programmatically), the honest limits, and
links to the architecture doc, this journal, the walkthrough, and the
full results file.

**Lineage, stated plainly.** The case study opens with a section I
should have written on day one: where Aegis comes from. It grows
directly out of the projects on the resume Jaspar holds. The
Agentic AI Framework for Genetic Evidence Discovery gave it the shape:
multi-agent orchestration and retrieval, evaluation designed first, and
an agent that declines what it cannot support with evidence. In Aegis
that rule becomes permission gates and honest aborts. The Reproducible
NGS Pipeline Development work, and the production pipeline platform at
Karyon Bio, gave it the discipline: QC and validation gates at every
stage become verify-after-step; exact reproducibility becomes
checkpoints and full traces; monitoring that catches failures before
users do becomes the trace store and the explorer. The browser
automation work, where grounding, verify-after-action, and failure
recovery were the actual problems, became the browser arm and the
recovery taxonomy. Same skills, same record, larger system. Nothing in
Aegis claims a domain that is not already on that resume.

### Publication

Before pushing anywhere, I swept the tree: no secrets, no .env (only
the placeholder .env.example), no personal data beyond my public name
and GitHub username, and only fictional example.test addresses from the
test fixtures. The repo is now public at
https://github.com/umeshlinga/aegis-agent, pushed from this machine
with the existing gh login. A stranger can clone it, run the demo, open
the explorer, and check every number in the README against the results
file. That was the point of keeping the traces-are-the-only-results
rule from day one.

### What remains before I show this to anyone

One honest gap, unchanged since Day 2: the adapters have never run
against a live provider, because there is no real API key in this
environment. The next step is a live-model run: a real model in the
planner seat over the same 30 tasks, scored the same way, with the
scripted-planner numbers standing as the runtime baseline it has to be
compared against. Until that run exists, every claim in the repo is
careful to say the v2 numbers measure the runtime, not a model's
planning. After it, the follow-on work is a larger suite with tasks I
did not design around the recovery taxonomy, and only then is this the
version I would put in front of a hiring team.
