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
