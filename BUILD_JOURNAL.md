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

## V2 Day 1: the product direction, the plan documents, and the first real-data layer

### The direction changed, and I am recording whose call it was

Until today Aegis was a runtime with a case study attached, built to
show how an agent system should behave. Today the instruction changed,
in the user's own terms: this has to become a real, massive product,
for himself, a foundation he can build a company and a product on, not
only an interview showcase. Real time data, not fake data. Real
problems fixed, with comparisons he can look at and show. Deeper into
the LLM and agent subject than the v1 runtime went, with everything
involved in one project, and bioinformatics integrated at the deepest
level, because that is his field and his edge. And documentation for
every step: what was taken, what was given, what tools were used, what
models, what commands, what the results mean.

I agree with the direction, and this tranche is the honest first step
of it, sized so it can be finished and verified today instead of
promised. Tranche 1 is: the product blueprint, the full tools and
models inventory, the comparison framework, and the first real code of
the product path, live clients for three public data sources. It is
not the whole product. Section (e) of PRODUCT_PLAN.md names the phases
that remain, each with acceptance criteria, and I would rather ship a
verified foundation with named gaps than a large claim with none.

### What I built today

**PRODUCT_PLAN.md.** The product in one page: an agent product for
genomic evidence work that runs real tasks end to end on live public
data, with permission gates, verified citations, and measured
reliability, used through a web app. Then the problems it attacks,
each mapped to the mechanism that answers it: hallucinated citations,
unverified answers, no recovery, no audit trail, agents acting
without approval, demos on fake data, confident answers on
insufficient evidence, cost and model opacity. Then the full v2
architecture, the build phases with acceptance criteria (live LLM
run, real-data wiring, RAG and citation checking, web app, comparison
run, deployment), and the documentation map that binds this journal,
the inventory, the comparison document, and the harness results files
into one record.

**TOOLS_AND_MODELS.md.** Every tool, model, library, and data source
the v2 product uses or will use, each with what it does here, why it
was chosen, what was rejected and why, and its key and cost facts.
Every row is marked IN USE NOW or PLANNED FOR V2, and the two
genuinely undecided pieces, vector retrieval and the web app stack,
are recorded as criteria for a decision that has not been made,
because writing down a choice I have not made would be the exact kind
of claim this project refuses to make.

**COMPARISON.md.** The market comparison framework, written before
any comparison run so the rules cannot be bent around results: the
same real bioinformatics tasks run three ways (a single plain LLM
call with no tools; an LLM with tools but no recovery and no gates;
full Aegis), scored on task success, citation correctness checked
against the source records, decline correctness on insufficient
evidence tasks, recovery rate on real failures, approval gate
violations (acceptable number: zero), and cost and latency per task.
Task design rules ban any task shaped around Aegis internals, and
ground truth comes from the sources, with ClinVar reclassifications
used deliberately. The document states plainly, and I repeat it here:
no comparison numbers exist yet, and none may be claimed until that
harness runs.

**The real-data layer, `src/aegis/data/` (752 lines across seven
modules, plus 315 lines of tests in three new test files).**
Live clients, standard library only, written plainly:

- `pubmed.py`: E-utilities esearch plus efetch. Returns PMID, title,
  abstract, journal, and year per article.
- `clinvar.py`: E-utilities esearch plus esummary. Returns the
  versioned accession, the HGVS title, the germline classification,
  review status, last evaluated date, and trait per variation.
- `gnomad.py`: the GraphQL variant lookup. Returns genome and exome
  allele count, allele number, and allele frequency for one variant
  in a named dataset.
- Shared modules: `errors.py` (one typed DataSourceError with a kind:
  http_error, timeout, network_error, parse_error, not_found,
  api_error), `http.py` (one injectable urllib transport, the same
  pattern as the model adapters), and `cache.py` (one JSON file per
  request under a caller chosen directory).

The rules every client follows, because the product depends on them:
every record carries the URL of its own page at the source, so any
citation built on it can be checked; a failed request raises a typed
error and never comes back as a silent empty result (an empty list
means the source genuinely matched nothing, and those are different
facts); every record states whether it came from a live fetch or the
cache, and when the live fetch happened, because ClinVar
reclassifies and "when did you read this" is part of the evidence.

### Decisions I made today, and what I considered instead

**Standard library only, again.** The model adapters set this pattern
in Phase 2 and it holds here: the E-utilities and GraphQL wire
formats are small and stable, tests inject a stub transport and
replay recorded responses, and there is no SDK between the reader
and the wire. Rejected: requests or httpx as the transport, and the
provider SDK approach generally. Revisit only if a source ever needs
something urllib cannot do, and record it here if that day comes.

**Tests replay recorded real responses, and the fixtures say so.**
Each fixture in tests/fixtures/data/ is a real response captured from
the real API today, with the query and date recorded in that
directory's README. Rejected: hand written invented JSON that looks
like a ClinVar record. A parser tested against invented shapes proves
nothing about the real source, and this project's fixtures rule is
the same as its results rule: real or labelled, never passed off.

**gnomAD dataset is a parameter, defaulting to gnomad_r4.** Releases
differ and frequencies differ between them, so the dataset is named
on every record and in its source URL rather than buried. This choice
earned its keep within the hour: the smoke variant below exists in
gnomad_r2_1 and genuinely does not exist in gnomad_r4, and the client
surfaced that as a typed not_found instead of a zero frequency. That
distinction is worth money in this domain.

**The cache stores raw response text, keyed by request, with the
fetch timestamp.** Rejected: storing parsed records (a parser change
would poison old evidence) and any expiry policy inside the cache
(freshness is a caller decision, made with the timestamp in view).

**Vector retrieval and the web stack are criteria, not choices.**
Both are recorded in TOOLS_AND_MODELS.md as decisions to be made at
the phase that needs them, judged against written criteria.
Rejected: naming a vector store or a web framework today to make the
plan look complete. A plan with two honest open decisions is worth
more than a plan with two invented ones.

### Commands I used today

- Fixture capture, one curl per response, into tests/fixtures/data/:
  `curl "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=BRCA1+variant&retmax=2&retmode=json"`,
  the matching efetch for PMID 43015897 in XML, the ClinVar esearch
  for `BRCA1[gene]` and esummary for variation 5042465, and two
  `curl -X POST https://gnomad.broadinstitute.org/api` GraphQL calls
  for variant 1-55516888-G-GA in datasets gnomad_r2_1 and gnomad_r4.
- Tests, data layer first, then the whole suite at the end of the
  tranche: `.venv/bin/python -m pytest tests/test_data_pubmed.py
  tests/test_data_clinvar.py tests/test_data_gnomad.py -q`, then
  `.venv/bin/python -m pytest -q` for the full suite.
- Live smoke checks: a short Python script run with the repo venv,
  calling the built clients directly (`search_pubmed`,
  `search_clinvar`, `lookup_variant`) against the live APIs with a
  throwaway cache directory under /tmp. Results below.

### Live smoke checks, run 2026-11-13, and what came back

One query per source, through the built clients, small on purpose.

- **PubMed, search "BRCA1 variant", top 2:** two live records.
  PMID 43015897, "Patterns of metastasis according to germline
  BRCA1/2 mutation status in patients with HER2-negative metastatic
  breast cancer", and PMID 43015421, a saturation genome editing
  study of BRCA1. Both with source URLs, fetch timestamp
  2026-11-13T19:27:28Z, from_cache False.
- **ClinVar, search "BRCA1[gene]", top 2:** two live records, both
  Pathogenic. VCV005042465.1, NM_007294.4(BRCA1):c.711del
  (p.Glu237fs), and VCV005042298.1, NM_007294.4(BRCA1):c.2814_2817dup,
  each with its ClinVar variation page URL.
- **gnomAD, and this one taught something:** in the first smoke run,
  the lookup for 1-55516888-G-GA in gnomad_r2_1 timed out at the 60
  second budget and came back as a typed timeout error, while the
  same variant in gnomad_r4 answered inside the same run with the
  real not_found described above. A retry of the r2_1 lookup with a
  90 second budget answered in about 7 seconds: genome allele
  frequency 0.004971002485501243 (allele count 156, allele number
  31382), exome allele frequency 0.0009436169383139671, fetched at
  2026-11-13T19:28:45Z. A second identical call in that run was
  served from the cache with the same fetch timestamp, which proves
  the cache path against a live source, not only against fixtures.
  The lesson, recorded for Phase 2: the gnomAD API's latency varies
  wildly, a typed timeout with a retry is the correct behaviour, and
  a silent empty result would have hidden a real answer. That is the
  recovery argument, happening on real infrastructure, on day one.

### The honest state after this tranche

- **Still scripted and local:** the v1 runtime eval. Its 30 of 30
  versus 17 of 30 numbers measure the runtime on local tasks with a
  scripted planner, exactly as the README's limits section says. The
  live planner run is Phase 1 of the new plan and has not happened.
- **Now real:** the data layer. PubMed, ClinVar, and gnomAD records
  in the product path come from the live public APIs, with source
  URLs, timestamps, cache provenance, and typed failures, proven by
  20 new fixture tests and the live smoke checks above.
- **Written but not yet run:** the comparison framework. No
  comparison numbers exist, and none are claimed anywhere.
- **What is next, and what it needs:** Phase 1, the live LLM run,
  needs a live Anthropic API key in the environment, which only the
  user can supply (through the secure capture, never through chat or
  the repo). With that key, the same 30 tasks rerun on a real planner
  and the product gets its first live model numbers. Phase 2 then
  wires today's data clients into the registry as read scope tools
  and builds the first real task set on live data.

## V2 Day 2: the evidence core, dossiers, citation checking, and the decline gate

### What I built today

Tranche 2 is the piece that turns the Tranche 1 data clients into a
product function: the evidence core in `src/aegis/evidence/` (813
lines across six modules, plus 415 lines of tests in three new test
files). It works on real data, end to end, and it involves no LLM at
all. That was a deliberate sequencing decision, not an omission: the
part of this product that must never depend on a model is the part
that decides whether the evidence is real and whether it is enough,
so I built that part as pure code over the live clients, and the
model written summary, when a live key arrives, will sit on top of a
dossier that has already passed it.

**dossier.py.** `build_evidence_dossier(query)` takes a gene and an
optional variant (an EvidenceQuery, or a string like "BRCA1" or
"BRCA1 17-43044295-G-A" that parse_query reads strictly: anything
beyond two words is rejected instead of guessed at). It asks ClinVar
for classifications in the gene, PubMed for the literature, and
gnomAD for population frequencies when a variant is named, and turns
each returned record into one evidence item: source name, record id,
title, a summary built from the record's own fields, the exact
source URL, fetched_at, and from_cache. The dossier also counts
items by source, and, the detail the whole tranche hangs on, it
snapshots `returned_keys`, the (source, record id) pairs the clients
actually returned in this run, recorded separately from the items.
A source that fails is written into `source_errors` in plain words
and the build continues with what the other sources returned. A
partial dossier that says what is missing is useful. One that hides
a failed source is not.

**citations.py.** The citation checker, and the anti-hallucination
gate this product is built around. Every item is checked three ways:
the URL is well formed and sits on the host that source actually
uses; the URL names the record the item claims (PMID in the PubMed
URL path, variation id in the ClinVar URL path, variant id plus a
named dataset in the gnomAD URL); and the item's key is in the
dossier's returned snapshot, meaning a client really returned that
record in this run. A failure removes the item and logs a
CitationFailure with the reason in plain words. The checker never
rewrites a URL or corrects an id into passing, because a checker
that fixes citations would hide the failures it exists to catch.

**gate.py.** The decline rule in code, as two named constants:
MIN_ITEMS 3, MIN_SOURCES 2. The gate runs on the verified items
only, never on the raw dossier, so an item the checker removed does
not count toward the bar. Below either bar the verdict is a decline
whose reason carries the actual numbers and names each missing piece
("Missing: 1 more verified item and evidence from 1 more source").
Nothing pads a thin dossier: the builder is not asked twice, items
are not duplicated, and a decline is a complete result.

**report.py and the command.** The report renderer produces the
Markdown a user reads: the question, the verdict with its reason,
the verified evidence grouped by source with each record's URL and
provenance line, a section naming any citations the checker removed
and why, a section for sources that did not answer, and a footer
recording per source item counts, fetch times, and cache status.
The command is `python -m aegis.evidence BRCA1` (with `--variant`,
`--limit`, `--cache-dir`, and `--out` when needed). It runs the
whole pipeline, writes the report, and prints where it went, the
counts, and the verdict. It uses the data layer's DiskCache by
default, so a repeat run is fast and the report says so.

### Decisions I made today

**The returned snapshot lives on the dossier, not in the checker.**
The checker needs ground truth about what the clients returned, and
that truth has to be recorded at build time, by the builder, before
any later hand (mine, a person's, a model's) can touch the items.
Keeping the snapshot as data on the dossier makes the fabrication
test honest: a test adds a plausible item whose key is not in the
snapshot, and the checker removes it for exactly the reason a real
hallucinated citation would be removed.

**Provenance is a snapshot, not a re-derivation.** I considered
having the checker re-fetch each cited record from its source to
prove it exists. Rejected for this tranche: it doubles network load,
makes checking depend on the source being up twice, and answers a
weaker question (the record exists somewhere) than the one that
matters (this run actually retrieved it, and the answer is built
from what this run retrieved). A live re-resolution check can join
later as a second layer; the snapshot check is the gate.

**The gate counts verified items, and its constants are named, not
buried.** Three items from two sources is a judgement call, and a
future reviewer should be able to find it, argue with it, and change
it in one place. It is the same bar my evidence agent work used, and
consistency across the two systems is worth more than a fresh guess.

**Source failures are notes on the dossier, never exceptions out of
the builder.** The CLI and the report both surface them. The one
exception shape I kept: a gnomAD not_found for a variant the query
named is recorded as a source note, never as a frequency of zero.
That distinction was the Tranche 1 lesson and it holds here.

### Commands I used today

- Tests as I built, evidence files first: `.venv/bin/python -m
  pytest tests/test_evidence_citations.py tests/test_evidence_gate.py
  tests/test_evidence_report.py -q` (23 passed).
- The live end to end run: `.venv/bin/python -m aegis.evidence BRCA1
  --out examples/evidence/evidence_brca1_2026-11-13.md`, then a repeat
  run of the same command to a scratch output to check the cache
  path, then a fresh cache regeneration of the saved example after a
  wording fix in the gate (below).
- Full suite at the end: `.venv/bin/python -m pytest -q`.

### The live BRCA1 run, and what came back

The run gathered 10 evidence items, all from live fetch on the first
run: 5 ClinVar records (four Pathogenic BRCA1 variants and one
Likely benign, accessions VCV005042465.1 and its neighbours, all
last evaluated in 2026) and 5 PubMed articles. gnomAD was not
queried, because the query named no variant, and the report says the
dossier rests on the two sources it used. The citation checker
verified all 10 and removed 0. The gate passed: 10 verified items
from 2 different sources, against a bar of 3 from 2. The report is
saved at examples/evidence/evidence_brca1_2026-11-13.md, exactly as
the command wrote it.

The repeat run is the cache proof against live sources, not only
against fixtures: the same command gathered the same 10 items, all
10 from cache, in a fraction of a second, with the original live
fetch timestamps preserved in the report footer.

One failure I hit and fixed in the open: the first rendered report
read "**Answerable.** Answerable: 10 verified items...", because the
gate's reason string and the report's verdict line both carried the
verdict word. I removed the prefix from the gate reasons (the
verdict word belongs to the report), regenerated the saved example
from a fresh cache so its fetch lines are an honest live run, and
the fixture tests were updated by the same change passing unchanged,
since they assert the numbers, not the prefix.

What did not happen, honestly: no variant query was run live in this
tranche, so the gnomAD path through the dossier builder is covered
by the data layer's live smoke from Tranche 1 and by fixture shaped
items in the checker tests, not by a live dossier run with a
variant. That is a named gap, and the first live variant dossier is
a small follow up, not a rewrite.

### The honest state after this tranche

- **Now real:** structured retrieval from three live sources into a
  dossier, citation checking with a fabrication gate, the decline
  rule in code, and a readable report, all runnable with no API key,
  proven by 23 new tests (including the fabricated citation that
  must be caught, and is) and one live BRCA1 run saved in the repo.
- **Still pending the key:** the LLM summary layer that writes prose
  on top of a verified, answerable dossier, the claim against record
  content check that belongs with it, and Phase 1's live planner run.
  PRODUCT_PLAN.md Phase 3 status now says exactly this split.
- **Still pending wiring:** Phase 2, the data clients as registry
  tools in the agent loop, with checker outcomes recorded in traces
  and the Phase 3 acceptance run on that task set.
## V2 Day 3: the product web app over the evidence core

### What I built today

The product surface, at last: `src/aegis/webapp/`, a local web app
that a person can open and use, runnable as `python -m aegis.webapp`
(port 8770, and it prints the URL on start). Three files, each with
one job. `pages.py` renders every page as HTML strings in one place.
`server.py` holds the routes, the form validation, the in-memory
session history, and the one function that runs a question through
the evidence core: build the dossier, check the citations, apply
the gate, store the report. `__main__.py` is the command, binding
the same disk cache the evidence command line uses.

The flow a person sees: the home page says what Aegis is in one
line (an evidence agent for genomic variant research that answers
only with verified sources or declines), offers a gene field and an
optional variant field with the gnomAD form as the hint, and lists
the reports this session has produced. Submitting runs the real
evidence core against live PubMed, ClinVar, and gnomAD, then lands
on the report page: the verdict at the top with its reason, the
verified evidence grouped by source with record titles, identifiers,
clickable source URLs, summaries, fetch times, and live versus cache
marks, the citations the checker removed with their reasons, and any
source that did not answer. A footer on every page says plainly
that this is a local product prototype, public data only, history
in memory, no accounts, no database.

Error handling got the same care as the happy path, because a
product is judged at its edges. An empty gene and a badly formatted
variant each re-render the form with the problem stated in words
and the typed values kept, so fixing the input is one edit. A
source failing inside the core never reaches the page as an error
at all: the dossier builder records it, and the report page shows
it under "Sources that did not answer". A failure outside that
shape is caught by the handler and shown as a plain message. There
is no stack trace page anywhere in this app.

All externally sourced text is escaped on the way into the page.
Titles and abstracts come from live APIs; they are untrusted input
that happens to be text, and treating them as HTML would be a
standing invitation to anyone who can get markup into a record.

### Decisions I made today, and what I considered instead

**Standard library only, again.** I considered Flask, which the
plan's stack decision point would have allowed, and a small
JavaScript front end. Both were rejected for the same reason the
explorer is stdlib: this repo runs anywhere Python runs, with no
dependency to install, pin, or break, and the product's value is in
the evidence pipeline behind the page, not in the page machinery.
Server side rendering, no build step, no framework. If a later
phase needs live streaming of a run, that is a reason to revisit
this, recorded here so the revisit is a decision, not drift.

**POST, redirect, GET.** A successful submit answers with a 303 to
the report's own URL. The report is then a page a person can
bookmark, reload, and link from the history list, and reloading it
never re-runs the evidence build.

**History in memory, on purpose.** The session history is a list on
the server object with a lock (the server is threaded). I did not
add a database or local storage: the plan's deployment phase is
where persistence would be decided, and a prototype that pretends
to remember more than it does would be dishonest in a way this
project keeps refusing to be. The pages say the history clears
when the server stops.

**Validation at the form boundary.** The gene must be one symbol;
the variant must be a gnomAD id or an HGVS string, the two shapes
the evidence layer actually understands. Anything else is refused
with the expected form shown, instead of being passed to three
live sources to be misread three different ways.

**The builder is injected, not imported by the handler.** The
handler takes a dossier builder callable; production binds the
evidence core's builder with the cache, and tests bind stubs. That
one seam is what lets the whole HTTP surface be tested with zero
network while the real checker and gate still run.

### Commands I used today

- Tests as I built: `.venv/bin/python -m pytest tests/test_webapp.py
  -q` (6 passed: form rendering, an answerable page with grouped
  evidence and verdict, a thin dossier showing the decline reason,
  validation re-rendering, history listing, the plain 404 page).
- Full suite: `.venv/bin/python -m pytest -q`.
- Live verification: started the real server with `.venv/bin/python
  -m aegis.webapp --port 8770`, then drove it with an HTTP client
  script: GET the home page, POST gene BRCA1 with no variant to
  /query exactly as the form does, follow the redirect, and read
  the result page; then GET the home page again for the history.

### The live verification, and what came back

The home page loaded with the form in it. The BRCA1 submission
redirected to /report/1, and that page carried the verdict
Answerable, ClinVar and PubMed groups, 10 real evidence links
(PubMed record pages and ClinVar variation pages), fetch times,
and live fetch marks on every item: this run fetched live, the
cache being empty at the start, which the page stated honestly
rather than claiming cache provenance it did not have. The home
page afterwards listed the BRCA1 report with its verdict and a
link back to /report/1. So the verification ran against live data,
not cache, and the product worked end to end through its own form.

### The honest state after this tranche

- **Now real:** a usable local product over the evidence core. A
  person who has never seen this repo can open it, ask a real
  question, and read a checked, sourced report or an honest
  decline, with every number on the page traceable to a record.
- **Still pending the key:** the LLM summary layer that writes
  prose on top of a verified dossier. Until there is a live API
  key, the report page shows the evidence and the verdict without
  a model-written summary, and says nothing it cannot source.
- **Still pending:** public deployment, which is Phase 6 of the
  plan, and the agent loop surfaces (live run view, approval
  prompts) that belong to Phase 2 wiring, not to this surface.
  PRODUCT_PLAN.md Phase 4 status now records this split.
