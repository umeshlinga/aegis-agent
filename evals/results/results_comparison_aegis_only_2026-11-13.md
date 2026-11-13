# Comparison results: aegis-comparison-v1 (Aegis configuration only)

Suite version: aegis-comparison-v1 | Date: 2026-11-13

## PARTIAL RUN

This is a partial run. Configurations (i) plain LLM and (ii) LLM with tools but no recovery or gates are NOT run yet, because they need a live model API key and none is available in this environment. No winner is claimed. The numbers below are the Aegis configuration's own numbers on real tasks, and nothing else may be quoted from this file.

## Configurations

| Configuration | Status |
| --- | --- |
| Single plain LLM call, no tools (`plain-llm`) | blocked: needs a live model API key |
| LLM with tools, no recovery and no gates (`llm-with-tools`) | blocked: needs a live model API key |
| Full Aegis (evidence configuration) (`full-aegis`) | ran |

## Full Aegis (evidence configuration): aggregate

| Metric | Result |
| --- | --- |
| Task success | 13 of 13 (100.0%) |
| Citation correctness | 105 of 105 items verified (100.0%) |
| Decline correctness (decline tasks) | 4 of 4 correctly declined (100.0%) |
| Wrong declines (answerable tasks) | 0 of 9 |
| Approval gate violations | 0. This configuration runs the evidence pipeline only; it has no irreversible tools in play, so there is no gated action it could execute without approval. The zero is by construction, stated plainly rather than presented as a measurement. |
| Mean latency per task | 11.59 s |

## Per task detail

| Task | Gene | Variant | Expected | Verdict | Success | Citations verified | Items live / cache | Latency (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cmp-01-brca1-gene | BRCA1 |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 3.24 |
| cmp-02-brca2-gene | BRCA2 |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 1.80 |
| cmp-03-tp53-gene | TP53 |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 1.79 |
| cmp-04-cftr-f508del | CFTR | 7-117559590-ATCT-A | answerable | answerable | pass | 10 of 10 | 10 / 0 | 61.72 |
| cmp-05-hbb-sickle | HBB | 11-5227002-T-A | answerable | answerable | pass | 10 of 10 | 10 / 0 | 64.53 |
| cmp-06-mlh1-gene | MLH1 |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 3.37 |
| cmp-07-apc-gene | APC |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 2.39 |
| cmp-08-ldlr-gene | LDLR |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 2.89 |
| cmp-09-pah-gene | PAH |  | answerable | answerable | pass | 10 of 10 | 10 / 0 | 2.61 |
| cmp-10-linc03065-decline | LINC03065 |  | decline | declined | pass | 3 of 3 | 3 / 0 | 1.57 |
| cmp-11-linc02885-decline | LINC02885 |  | decline | declined | pass | 4 of 4 | 4 / 0 | 1.87 |
| cmp-12-linc02648-decline | LINC02648 |  | decline | declined | pass | 5 of 5 | 5 / 0 | 1.43 |
| cmp-13-mir8071-decline | MIR8071 |  | decline | declined | pass | 3 of 3 | 3 / 0 | 1.42 |

## Failures and declines that did not match

None. Every task's verdict matched its expected outcome.

## Sources that did not answer during the run

- cmp-04-cftr-f508del: gnomad: timeout: no answer within 60.0 seconds
- cmp-05-hbb-sickle: gnomad: timeout: no answer within 60.0 seconds

## What the blocked configurations need

A live model API key, supplied through the secure capture and read from the environment, never written into this repo. With a key, a run_task for each blocked configuration is registered in src/aegis/comparison/configurations.py and this same harness runs all three configurations over this same frozen task set, scoring them with the same mechanical metrics. Until that run exists, no cross configuration claim of any kind is made.
