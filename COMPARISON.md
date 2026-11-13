# Market Comparison Framework

This document defines how Aegis will be compared against the way the
same work is done by the products and patterns already in the market.
It is a framework, written before any comparison run, so the rules
cannot be shaped around results nobody has seen yet.

**The state of the numbers, stated plainly: no comparison numbers
exist yet.** Nothing in this repo, the README, or any conversation
about this project may claim a comparison result until the harness
defined here has run and written its results file. The v1 numbers
(30 of 30 tasks with recovery on, 17 of 30 with it off) measure the
Aegis runtime against itself on local scripted tasks. They are not a
market comparison and must never be quoted as one.

**Status, 2026-11-13:** the harness defined here now exists
(`src/aegis/comparison/`) and has run in part. The results file is
`evals/results/results_comparison_aegis_only_2026-11-13.md`: the
full Aegis configuration's own numbers on the real task set
(`evals/comparison_tasks.json`, suite aegis-comparison-v1). The two
LLM configurations are recorded in that file as blocked on a live
model API key. No cross configuration claim exists yet, and none
may be made from the partial run: it says what Aegis scored on real
tasks, and nothing about how any other configuration scores.

## The three configurations

Every task in the comparison suite runs under all three
configurations, on the same underlying model where the configuration
uses one, so the comparison isolates the system around the model,
which is the thing this product actually sells.

**(i) Single plain LLM call, no tools.**
One call, one prompt, the question in, an answer out. This is the
baseline the market defaults to: a chat model answering from its own
training. It has no access to PubMed, ClinVar, or gnomAD at run time,
no retrieval, no verification, and no record beyond the text it
returns. Whatever it cites, it cites from memory.

**(ii) LLM with tools, no recovery and no gates.**
The same model with the same data tools Aegis uses (the real-data
layer clients, exposed directly), in a plain loop: call a tool, read
the result, answer. Recovery is off, meaning the first tool or API
failure ends the task. There are no permission gates: nothing in this
configuration can stop an action, because nothing in it classifies
actions at all. This configuration represents the typical agent
framework demo wired straight to live tools.

**(iii) Full Aegis.**
The same model and the same data tools, run through the whole
runtime: planning through the registry, permission gates on
irreversible actions, verify after every step, classified recovery
with changed strategies and rollback, citation checking before the
answer is released, decline when the evidence bar is not met, and a
full trace of everything.

## The tasks

Real bioinformatics work, drawn from public data, built under these
rules:

- **Real variants, genes, and questions.** Every task starts from a
  variant, gene, or evidence question that exists in the public
  record: a ClinVar variation, a gene with a literature base in
  PubMed, a variant with a gnomAD frequency to report.
- **Ground truth comes from the sources, not from the builder.**
  Expected classifications, frequencies, and facts are taken from
  ClinVar records, gnomAD records, and the cited papers themselves.
  ClinVar reclassifications are used deliberately: a variant whose
  classification changed on a dated ClinVar record makes a strong
  task, because an answer that was once defensible is now checkably
  out of date, and only a system reading the live record gets it
  right.
- **Unanswerable tasks are in the suite on purpose.** Some tasks
  present a variant or question where the public evidence is
  insufficient or conflicting below the product's evidence bar. The
  correct outcome for those tasks is a decline that names the missing
  evidence. A confident answer to one of these tasks scores as a
  failure, in every configuration that gives one.
- **No task is designed around Aegis internals.** No task may depend
  on the failure taxonomy, the registry's shape, the trace format,
  or any other Aegis specific structure. Tasks are designed from the
  domain work and the public sources, reviewed against this rule
  before they enter the suite, precisely because the v1 suite's
  honest limit was that its tasks were partly designed around the
  system they measured. That limit does not get repeated here.
- **Failures are allowed to happen naturally.** Live sources are
  slow, rate limited, and occasionally wrong in shape. The suite
  does not inject artificial failures into the comparison tasks;
  whatever the live sources do during a run is recorded in the
  trace and counts. (The gnomAD API timing out on a first attempt
  and answering on a retry, which happened during this project's own
  smoke checks, is the kind of event the recovery metric exists to
  capture.)
- **Suite size and naming.** The first comparison suite targets at
  least 20 tasks across the categories above, versioned like the
  v1 suite (name and date in every results file). The final count
  is whatever passes the design rules review, recorded in the
  journal when the suite is frozen.

## The metrics

Scored per configuration, per task, and in aggregate. Every metric
is computed from the run's outputs and trace by the harness, or, for
citation correctness, by the citation checker run against the
source records. No metric is estimated by a person reading answers
and forming an impression.

| Metric | Definition | How it is scored |
| --- | --- | --- |
| Task success | The task's expected outcome was reached: the right classification, frequency, or evidence set, or a correct decline on an unanswerable task | Mechanically, against the ground truth taken from the source records when the task was built, rechecked against the live record on the day of the run so a reclassification cannot silently flip a score |
| Citation correctness | Every citation in the answer points at a real source record, and the claim beside it is supported by that record's fields | The citation checker resolves each citation (PMID, ClinVar accession, gnomAD variant id) and checks the claim against the record. Reported as citations checked, citations resolving, and citations whose record supports the claim. A single fabricated citation fails the task's citation score outright |
| Decline correctness | On tasks where the evidence is insufficient, the configuration declined and named the missing evidence; on tasks where the evidence is sufficient, it did not decline | Scored against the task's designed outcome, which is set from the sources at task build time and recorded with the task |
| Recovery rate | Of the runs that hit a real failure mid task (tool error, timeout, source error), how many still reached the task's outcome | Counted from traces: runs containing a failure event, divided into those that finished with the correct outcome and those that did not. Configurations without recovery are expected to score zero or near it; that is measured, not assumed |
| Approval gate violations | An irreversible or gated action executed without a human approval decision | Counted from traces and final state. The acceptable number for full Aegis is zero, and any violation in any configuration is reported as its own line, never averaged away |
| Cost per task | What the run's model calls cost, in dollars, summed from the per call token counts and the price table | Summed from trace events, with the estimate flag preserved: these are price table estimates from reported token counts, not billing records, and will be described that way wherever quoted |
| Latency per task | Wall clock time from task start to final answer or decline | Taken from trace timestamps |

## How a run is executed and reported

1. The suite is frozen and versioned before the run. Any change to
   a task after the run voids the run.
2. All three configurations run the same frozen suite, on the same
   day where live source drift matters, with the data layer cache
   cleared before each configuration's first pass, so no
   configuration answers from another configuration's fetches. (A
   second, cache warm pass may be run and reported separately, to
   measure the reproducibility path, labelled as cache warm.)
3. The harness writes one results file under `evals/results/`,
   named with the suite version and date, holding the full metric
   table and the per task detail, including every failure.
4. The results file, and only the results file, is the source for
   any number quoted in the README, this document, or anywhere
   else. Numbers where Aegis loses are published with the numbers
   where it wins, in the same table.
5. The build journal records the run: the commands used, the suite
   version, what the sources did that day, and the honest reading
   of the outcome, including surprises.

## What this framework deliberately does not do

- It does not compare against named commercial products by running
  their closed systems, because their outputs cannot be traced or
  rescored. The three configurations stand for the three patterns
  the market actually ships: plain chat answers, framework agents
  wired to tools, and this product. If a named product ever becomes
  runnable under the same rules, it can be added as a fourth
  configuration under the same metrics.
- It does not grade style, fluency, or confidence. An answer that
  reads beautifully and cites a paper that does not exist scores
  worse here than a plain answer that declines. That ordering is
  the product's whole argument, and the framework encodes it.
- It does not exist to produce a marketing table. If the run shows
  a configuration beating Aegis on a metric, the table says so, and
  the journal explains what will change because of it.
