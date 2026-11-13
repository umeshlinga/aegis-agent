# Evaluation

## What is compared

Systems are feature sets over identical code, prompts and models, so a difference is attributable to the feature.

| System | Retries | Degrade | Verifier | Judges | Grounding | Self-consistency | Abstain | Parallel |
|---|---|---|---|---|---|---|---|---|
| baseline | – | – | – | – | – | – | – | 1 |
| aegis | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6 |
| no_verifier | ✓ | ✓ | – | – | ✓ | ✓ | ✓ | 6 |
| no_retries | – | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6 |
| no_degrade | ✓ | – | ✓ | ✓ | ✓ | ✓ | ✓ | 6 |

Fault profiles (seeded, identical across systems): `none`; `mild` (3% timeout, 3% 429, 2% 5xx, 2% malformed per call); `harsh` (10/10/6/6%); `outage` (PubMed search and gnomAD constraint return 5xx for the whole run); `crash_mid` (process dies during `verify`; Aegis resumes in a new runtime, the baseline restarts from scratch and both attempts are charged).

## Metrics

| Metric | Definition |
|---|---|
| success_rate | run reached `succeeded`; Wilson 95% CI |
| recovery_rate_under_faults | success among runs that received at least one injected fault (or crash) |
| citation_validity | cited functional items whose PMID was retrieved and whose quote is verbatim in that abstract, computed from raw step outputs whether or not the verifier ran |
| reports_with_ungrounded_numbers | reports containing a number absent from the evidence JSON |
| accuracy_3class_answered | P/LP vs VUS vs B/LB agreement with ClinVar on answered cases (live only) |
| coverage, abstain_rate | share answered; share declined |
| AUROC P/LP vs B/LB | ACMG points as the score; bootstrap 95% CI (live only) |
| ECE, Brier | after Platt scaling fitted on the TRAIN split, measured on TEST (live, `--calibrate`) |
| coverage–accuracy curve | accuracy when only answering with abs(points) ≥ t |
| cost, LLM calls, p50/p95 wall time | from persisted spans (survive crashes) |
| paired test | exact McNemar on per-case success, baseline vs aegis, same cases and faults |

## Offline results (13 Nov 2026)

`docs/results/offline-2026-11-13/` (report.html, summary.json, runs.jsonl). 60 synthetic cases per cell, 1,500 runs total.

* Success: Aegis 100% in every profile. Baseline 100 / 58 / 18 / 0 / 52% for none / mild / harsh / outage / crash_mid. All non-trivial differences p < 1e-7 (exact McNemar).
* Attribution from ablations: without retries, harsh drops to 53% and mild to 87%; without degradation, the outage drops to 0%; without retries the crash profile drops to 75% (faults on the resumed path).
* Citations: baseline 88% valid with no faults (it accepts injected fake PMIDs and paraphrased quotes); Aegis 100%. `no_verifier` also scored 100%: majority voting over three triage samples already removed the injected citations because they did not repeat across samples. The verifier is the backstop for errors that do repeat.
* Ungrounded numbers in reports: baseline 16/60, Aegis 0/60.
* Cost of reliability: Aegis makes ~6.3 LLM calls per variant vs 3.0 for the baseline (self-consistency and judges). In the outage profile, retrying a dead upstream costs latency (p95 1.38s vs 0.07s without retries, in compressed time); the breaker limits it, and an outage-aware fail-fast is a listed improvement.

**What these numbers are not:** the fixtures and the stand-in model are synthetic; accuracy and AUROC on them are meaningless and are hidden from the offline report on purpose.

## Live benchmark (run with keys)

```bash
make bench            # ClinVar variant_summary -> evals/data/clinvar_benchmark.jsonl
                      # GRCh38, 2+ star review, P/LP/VUS/B/LB, per-gene cap 3,
                      # train = last evaluated <= 2024-06-30, test = after
make eval-live        # --mode record: every model response saved; rerun with --mode replay for free
aegis judge-calibrate --labels evals/data/judge_labels.jsonl   # after hand-labelling ~50 rows
```

Report live numbers as: n, CI, model IDs, date, cutoff, and cost per variant. Compare with v1's reported AUROC 0.968 on its own temporal split, and say plainly if v2 is lower on this (different) benchmark.

## Turning failures into tests

`aegis export-failures` writes every failed, degraded, or evidence-rejected run (with failure labels and replayable input) to `evals/data/regressions.jsonl`. Those cases are rerun on every change.
