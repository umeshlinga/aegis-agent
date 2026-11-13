# Aegis walkthrough: presenter script

A guided demo of the Aegis runtime in about five minutes. Everything
runs locally against the project's own hostile test site, and every beat
is scored by the same mechanical scorers the eval suite uses, so what
the audience sees is checked, not staged.

Where this comes from, in one breath, if the audience asks: Aegis is
the bigger build that grows out of the projects on my resume: the
multi-agent Agentic AI Framework for Genetic Evidence Discovery
(LangChain, LangGraph, retrieval, evaluation designed first, an agent
that declines what it cannot support), the Reproducible NGS pipeline
platform work with its QC and validation gates, and my agent and
browser-automation work on grounding, verification, and failure
recovery. Same skills, larger system, measured the same way.

## Before you start

One-time setup, from the repo root:

```bash
make setup
```

The one command for the whole demo:

```bash
python -m aegis.demo
```

(or `make demo`)

Say this first, out loud, so the framing is honest: "The planner in
these runs is a scripted model with a fixed plan per task. What is on
show is the runtime around the model: the permission gates, the browser
arm, and the recovery engine. The eval results at the end are measured
the same way."

## Beat 1: the button changes its label mid-task

**What to run:** nothing extra; it is the first beat of the demo output.

**What the audience sees:** the agent opens the page and plans two
steps. The click on the "Proceed to summary" button fails: by the time
the agent acts, the page has renamed the button, and the lookup by role
and label finds nothing. A RECOVERY line appears with the diagnosis
(element_not_found) and the strategy. The retry succeeds, the step is
verified, and the run finishes done. The scorer confirms it: PASS.

**The one-line explanation:** "A failed step is classified, not just
retried. The retry changes strategy: the browser arm re-grounds the
control by its role and its current label instead of repeating the
lookup that just failed."

## Beat 2: the form rejects the first submit

**What the audience sees:** the agent fills the entry form and clicks
submit. The site rejects the first submission with a transient error,
so the expected confirmation text never appears. Verification refuses
to mark the step done, recovery classifies the failure, the retry
submits again, and this time the confirmation appears and the step
verifies. The scorer checks the server's own submission records, not
the page text, and marks PASS.

**The one-line explanation:** "Verification is a separate step with its
own evidence. The agent does not get to believe a submit worked; it has
to see the result, and when it cannot, recovery treats it as a failure
to fix, not a success to report."

## Beat 3: the permission gate, refused then approved

**What the audience sees:** the same 25 dollar charge, twice. First
run: no approval function is configured. A PERMISSION GATE line appears
at the charge step: the tool is spend-scope and irreversible, the gate
refuses before the tool handler ever runs, the diagnosis states that a
denial is final, and the run ends failed with the charge ledger empty,
which the scorer asserts. Second run: an approval function grants the
charge. The same plan completes and the ledger holds one entry.

**The one-line explanation:** "The gate is code in the tool registry,
not an instruction in a prompt. The model cannot argue with it, retry
around it, or be talked out of it; an irreversible action happens only
when a human-approved path says yes."

## Closing: show the same runs as timelines

```bash
python -m aegis.explorer
```

Then open http://127.0.0.1:8765/ in a browser. The explorer lists runs
with their task, mode, outcome, and duration. Open the renamed-button
run: the timeline shows the plan, the model call, both click attempts,
the recovery event highlighted in amber, and the verification. Open a
refused charge or refused delete run: the permission-gate event is
highlighted in red.

**The closing line:** "The full 30-task suite, run with recovery on and
off, is in evals/results/results_aegis-suite-v2_2026-11-13.md: 30 of 30
with recovery on, 17 of 30 with it off, and every task with a
designed-in failure sits exactly on that split. The limits are written
down in the same repo, in the README's honest limits section: scripted
planner, local tasks, adapters not yet verified against live providers.
That is what a live-model run has to beat."
