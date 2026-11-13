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
