# Aegis eval (offline, 60 cases)

> OFFLINE run: synthetic fixtures + deterministic stand-in model with injected hallucinations. These numbers measure the runtime (recovery, verification, grounding), NOT real variant classification accuracy.

| system | profile | n | success_rate | success_ci | recovery_rate_under_faults | citation_validity | reports_with_ungrounded_numbers | mean_llm_calls | p95_wall_s |
|---|---|---|---|---|---|---|---|---|---|
| aegis | crash_mid | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 6.22 | 0.1 |
| aegis | harsh | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 6.27 | 0.132 |
| aegis | mild | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 6.27 | 0.089 |
| aegis | none | 60 | 1.0 | [0.94, 1.0] | None | 1.0 | 0 | 6.27 | 0.081 |
| aegis | outage | 60 | 1.0 | [0.94, 1.0] | 1.0 | None | 0 | 2.2 | 1.384 |
| baseline | crash_mid | 60 | 0.517 | [0.393, 0.638] | 0.517 | 0.857 | 7 | 1.55 | 0.16 |
| baseline | harsh | 60 | 0.183 | [0.106, 0.299] | 0.0 | 1.0 | 1 | 0.55 | 0.082 |
| baseline | mild | 60 | 0.583 | [0.457, 0.699] | 0.0 | 0.87 | 10 | 1.75 | 0.039 |
| baseline | none | 60 | 1.0 | [0.94, 1.0] | None | 0.882 | 16 | 3.0 | 0.128 |
| baseline | outage | 60 | 0.0 | [0.0, 0.06] | 0.0 | None | 0 | 0.0 | 0.017 |
| no_degrade | crash_mid | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 6.22 | 0.078 |
| no_degrade | harsh | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 6.27 | 0.203 |
| no_degrade | mild | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 6.27 | 0.078 |
| no_degrade | none | 60 | 1.0 | [0.94, 1.0] | None | 1.0 | 0 | 6.27 | 0.091 |
| no_degrade | outage | 60 | 0.0 | [0.0, 0.06] | 0.0 | None | 0 | 0.0 | 1.345 |
| no_retries | crash_mid | 60 | 0.75 | [0.628, 0.842] | 0.75 | 1.0 | 0 | 4.63 | 0.041 |
| no_retries | harsh | 60 | 0.533 | [0.409, 0.654] | 0.451 | 1.0 | 0 | 2.08 | 0.048 |
| no_retries | mild | 60 | 0.867 | [0.758, 0.931] | 0.636 | 1.0 | 0 | 4.77 | 0.088 |
| no_retries | none | 60 | 1.0 | [0.94, 1.0] | None | 1.0 | 0 | 6.27 | 0.024 |
| no_retries | outage | 60 | 1.0 | [0.94, 1.0] | 1.0 | None | 0 | 2.2 | 0.07 |
| no_verifier | crash_mid | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 5.22 | 0.054 |
| no_verifier | harsh | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 5.27 | 0.179 |
| no_verifier | mild | 60 | 1.0 | [0.94, 1.0] | 1.0 | 1.0 | 0 | 5.27 | 0.08 |
| no_verifier | none | 60 | 1.0 | [0.94, 1.0] | None | 1.0 | 0 | 5.27 | 0.065 |
| no_verifier | outage | 60 | 1.0 | [0.94, 1.0] | 1.0 | None | 0 | 2.2 | 1.371 |

Paired exact McNemar test on run success (same cases, same faults):

- crash_mid: aegis-only successes 29, baseline-only 0, p = 3.7e-09
- harsh: aegis-only successes 49, baseline-only 0, p = 3.6e-15
- mild: aegis-only successes 25, baseline-only 0, p = 6e-08
- none: aegis-only successes 0, baseline-only 0, p = 1
- outage: aegis-only successes 60, baseline-only 0, p = 1.7e-18
