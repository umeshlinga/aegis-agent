# Aegis Interview Defense Pack
## For Umesh Linga, Artisan conversation with Jaspar Carmichael-Jack and team

Use these as short anchors in your own words. Do not read them out loud word for word. Each bullet points to the real file you can open or name if they ask for proof.

Repo: https://github.com/umeshlinga/aegis-agent
Local copy: ~/workspace/aegis-agent/

---

## 1. 60-second pitch: what Aegis is

- Say this in your own words: Aegis is a runtime for agents that have to do real multi-step work and cannot be allowed to fail quietly or do something irreversible without a human yes.
- Three rules, all in code, not in prompts. Point to README.md, opening section.
  - Nothing irreversible runs without approval. Every tool declares a scope in src/aegis/tools/registry.py: read, write, act, spend.
  - Every step is verified before the run moves on. See ARCHITECTURE.md section (a) and (c).
  - Failures are classified and recovered with a changed strategy, or the run stops and says why. See src/aegis/recovery/engine.py and ARCHITECTURE.md section (e).
- It has a browser arm that works by role and label, not by coordinates or CSS ids. File: src/aegis/tools/browser.py.
- It has a model router that picks a model per step kind and logs cost and latency for every call. Files: src/aegis/models/router.py, src/aegis/models/adapter.py.
- Every run writes a full JSONL trace. File: src/aegis/trace/store.py. The trace explorer at src/aegis/explorer/ lets you open any run as a timeline.
- Close with the honest limit, say it early before they find it: the published run used a scripted planner, so the numbers prove the runtime, recovery, and gates. They do not yet prove live model planning. That rerun is next, once a live key is in. Source: README.md, Honest limits section.

## 2. Lineage: how this grows from the resume Jaspar already holds

Resume on file with Jaspar: ~/workspace/lab-outreach/resumes/Umesh_Linga_Resume_AIEngineering.pdf
Its two projects are (1) Agentic AI Framework for Genetic Evidence Discovery and (2) Reproducible NGS Pipeline Development.

- From the genetic evidence agent:
  - That agent was built with LangChain and LangGraph, with retrieval, and it was designed to decline a variant it could not support with evidence instead of guessing.
  - In Aegis that same rule becomes permission gates and honest aborts. If the system cannot justify the action, it stops. Point to README.md, Case study, Where Aegis comes from.
  - Evaluation was designed first there, on held-out data. Aegis keeps that habit: numbers only come from the eval harness. File: evals/results/results_aegis-suite-v2_2026-11-13.md.
- From the NGS pipeline platform work:
  - That work had QC and validation gates at every stage, and runs that reproduce exactly.
  - In Aegis those gates become verify-after-step, and reproducibility becomes checkpoints after every verified step plus full traces. Files: src/aegis/core/state_machine.py, src/aegis/core/checkpoints.py.
  - The production platform at Karyon Bio is where monitoring caught a failed run before a user did. In Aegis that becomes the trace store and the explorer.
- From browser automation work:
  - The hard problems there were grounding, checking after every action, and recovering mid-task when the page changed.
  - That is now the browser arm and the recovery taxonomy. Files: src/aegis/tools/browser.py, src/aegis/recovery/engine.py.
- One line to use if they ask why a new project: same skills, larger system, taken out of the genomics domain so the runtime itself can be judged on its own.

## 3. Jaspar ask 1: walkthrough or project link for the agent system you personally built

- Link: https://github.com/umeshlinga/aegis-agent
- Walkthrough script you can follow live: demo/WALKTHROUGH.md. One command: python -m aegis.demo, or make demo.
- Three beats in the demo, each scored by the same scorers as the eval suite:
  - Beat 1: button label changes mid-task, agent recovers.
  - Beat 2: form rejects the first submit, agent recovers and the server record proves it.
  - Beat 3: a charge is refused without approval, then completes when approval is granted.
- Build record in your own words: BUILD_JOURNAL.md, Day 1, Day 2, and Day 3 entries. Decisions and rejected alternatives are written there at the time, not after.
- Full design: ARCHITECTURE.md, sections (a) to (g).

## 4. Jaspar ask 2: is it used in production

- Answer honestly, in one breath: Aegis itself is a new personal build. It is public, it runs locally end to end, and it is not deployed in production at a company. Do not claim otherwise.
- What is in production is the work on the resume Jaspar holds: the pipeline platform at Karyon Bio that research teams run daily, with CI/CD, Airflow orchestration, monitoring, logging, and alerting, and the clinical pipeline work at Cipla behind reports physicians acted on.
- Bridge line you can use: Aegis takes the production discipline from that work, gates, verification, monitoring, reproducibility, and rebuilds it as a general agent runtime so it can be inspected and measured in the open.
- If they push on what production would still need: a live model in the planner seat, a real approval queue wired to a human, larger task suites not designed around the recovery taxonomy, and provider adapters verified against live keys. All of this is written in README.md, Honest limits, and BUILD_JOURNAL.md, Day 3 closing section.

## 5. Jaspar ask 3: a tricky execution or recovery failure you fixed, and how you measured the improvement

Use the renamed button story first. It is Beat 1 in demo/WALKTHROUGH.md and task b02-renamed-button in the results file.

- What happens: the agent plans a click on a button by its role and label. The test site renames the button shortly after the page loads, in src/aegis/testsite/server.py. The lookup finds nothing. Failure kind: element_not_found.
- What a simple system does: retries the same lookup, or dies.
- What Aegis does: the recovery engine in src/aegis/recovery/engine.py classifies the failure, and the browser arm in src/aegis/tools/browser.py changes strategy on the retry. It re-grounds the control by role and current label, with a longer wait, instead of repeating the failed lookup.
- How it was measured: the same task runs twice in the harness, recovery on and recovery off. File: evals/results/results_aegis-suite-v2_2026-11-13.md.
  - b02-renamed-button: pass with recovery on, FAIL with recovery off.
  - The twin story is the transient submit, task b03-form-transient-rejection and Beat 2 in demo/WALKTHROUGH.md. The site rejects the first submit per session. Verification refuses to mark the step done because the confirmation never appears. Recovery classifies it, retries, and the second submit lands. The scorer checks the server submission record, not the page text. Pass with recovery on, FAIL with recovery off, with the server fields empty in the off run.
- Headline numbers from the same file, suite aegis-suite-v2, 30 tasks:
  - Recovery on: 30 of 30 passed, 100.0 percent.
  - Recovery off: 17 of 30 passed, 56.7 percent.
  - All 13 tasks with a designed-in mid-task failure pass only with recovery on. The benign and safety tasks pass in both modes.
- Say the limit with the numbers, every time: the planner in this run was the scripted fake adapter, model name fake-scripted in src/aegis/models/fake.py. So this measures the runtime and recovery, not a live model planning its way through an unfamiliar task.

## 6. Jaspar ask 4: browser or computer-use automation you have built

- In Aegis: src/aegis/tools/browser.py, built on Playwright with Chromium.
  - Grounding is semantic: get_by_role and get_by_label. Never CSS ids, never raw coordinates.
  - Every action verifies inside the tool: a click must change the page or reach the expected text, a fill must read back the value it set.
  - Failures are typed, element_not_found, stale_state, timeout, tool_error, verify_failed, and the kind is what the recovery engine acts on.
- Hostile test site it was measured against: src/aegis/testsite/server.py. Deterministic behaviors include a button rendering late, a label changing after load, a form that rejects the first submit, a list that rebuilds in rotated order so handles detach, an endpoint that returns 503 once, and an endpoint that is slow once.
- Browser tasks in the suite: 16 of the 30 tasks in evals/results/results_aegis-suite-v2_2026-11-13.md, tasks b01 to b16.
- Live demo for this ask: Beat 1 and Beat 2 in demo/WALKTHROUGH.md, then open the same runs in the trace explorer with python -m aegis.explorer and show the recovery event highlighted in amber.
- Prior work anchor from your record: agent and browser automation where grounding, verify-after-action, and mid-task recovery were the actual problems. Aegis is that work rebuilt as a measured runtime.

## 7. San Francisco question

- Jaspar asked: would working in person in San Francisco suit you.
- Your answer: yes. You are willing to relocate anywhere in the United States the role requires, you move yourself, and in-person work in San Francisco works for you.
- Keep it short and clear. Do not add conditions in the same sentence.

## 8. Likely hard follow-ups

### Why permissions in code, why not just a careful prompt

- A prompt is a suggestion to a model. A registry check is code that runs every time. If the model is confused, jailbroken, or just wrong, the gate still holds. Point to src/aegis/tools/registry.py and ARCHITECTURE.md section (b).
- There is no default yes. With no approval function configured, an irreversible tool is blocked outright.
- A denial is final in the recovery engine. The agent cannot retry around it, rephrase past it, or split the action into smaller pieces. File: src/aegis/recovery/engine.py.

### Why verify after every step

- A tool call returning is not proof the work happened. A click can do nothing, a submit can be rejected, a write can land in the wrong place.
- Verification is a separate check on evidence: expected text on the page, a read-back value, or the server record. Only a verified step gets a checkpoint. Files: ARCHITECTURE.md sections (a) and (c), src/aegis/tools/browser.py.
- Without it, errors are found three steps later, if at all, and recovery has no clean state to return to.

### Why changed-strategy recovery, why not simple retry

- Repeating the identical call that just failed mostly repeats the failure. The journal records this as a deliberate rejection. See BUILD_JOURNAL.md, Day 1, Recovery as a taxonomy.
- The strategy follows the failure kind: re-ground for element_not_found, re-read state for stale_state, longer wait for timeout, backoff for a transient tool_error. The engine decides whether to retry, the tool arm makes the retry different. See BUILD_JOURNAL.md, Day 2, browser arm section.
- Retries are bounded. When the budget is spent on a state-shaped failure, the run rolls back to the last verified checkpoint, then aborts honestly with the diagnosis in the trace.

### Cost routing: why a router, and how is cost controlled

- Planning, acting, verifying, and diagnosing are different jobs. Planning wants the strongest reasoner. Verification wants a fast, literal checker. The router picks a model per step kind: plan, act, verify, diagnose. Files: src/aegis/models/router.py, ARCHITECTURE.md section (d).
- Each provider sits behind one adapter interface: src/aegis/models/adapter.py, with real adapters in src/aegis/models/http_adapters.py for Anthropic, OpenAI, and a local Ollama-style endpoint.
- Fallback chains are ordered per step kind. A provider error moves to the next adapter and is written to the trace as a model_call_failed event, so a run that finished on a weaker model says so in its own record.
- Every call logs tokens, latency, and cost in the trace. Cost is computed from a local price table in src/aegis/models/pricing.py and flagged cost_is_estimate, so an estimate is never mistaken for a billed figure.
- Honest note: in the v2 run the fake model costs zero, so the published table has no real spend in it. Real cost numbers only exist after the live run.

### What breaks next, what are the weak points

- Name them before they do, all from README.md, Honest limits:
  - The planner was scripted. Live model planning is unproven in this repo until the rerun.
  - The provider adapters are verified against stub transports and by clean failure without keys, not against live providers yet.
  - The tasks are local and small, and the hostile behaviors were chosen to exercise the taxonomy, so the suite partly tests whether the system survives its own designer traps.
  - Timings are from one machine.
- Next risks you would expect at larger scale: plans that are wrong in ways verification cannot catch with a substring check, approval fatigue if too many actions ask for a human, and recovery strategies that work on a test site but not on a real product surface that changes weekly.

### What would you do with a live key

- Put a real model, Anthropic Claude, in the planner seat and rerun the same 30 tasks with the same scorers, recovery on and off. The scripted numbers become the baseline the live run has to be compared against. This is the next step named in BUILD_JOURNAL.md, Day 3 closing section, and README.md, Status.
- Then grow the suite with tasks not designed around the recovery taxonomy, so the system is tested on failures nobody planned for.
- Only after that would you quote planning numbers. Until then, quote only runtime numbers and label them as such.

### What would you do in production at Artisan

- Start from the gates, not the model: declare every tool Ava can touch with a scope and an irreversibility flag, and put sends, charges, and deletions behind a human approval path from day one. That is the registry pattern in src/aegis/tools/registry.py.
- Verify after every outward action against the system of record, not against the agent report of what it did.
- Trace everything from the first run, and build the eval suite from real failed tasks, scored mechanically, with recovery on and off, before scaling volume.
- Keep the decline rule from the evidence agent: when the system cannot support the action with evidence, it stops and hands the case to a human with the trace attached, instead of guessing.

### Why a state machine first, not a graph framework first

- The states and transitions needed to be visible in the trace from day one: plan, act, verify, recover, done, failed. File: src/aegis/core/state_machine.py.
- The runner interface is shaped so a graph library such as LangGraph can replace the internals later without changing the contract. Recorded in BUILD_JOURNAL.md, Day 1.

### How do you know the demo is not staged

- The demo runs on the eval harness runner, so each beat is scored by the same mechanical scorers as the suite: final state plus trace assertions. See demo/WALKTHROUGH.md, opening section.
- The trace explorer at src/aegis/explorer/ shows the raw JSONL events behind each run. Anyone can clone the public repo, run make demo, make eval, and make test, and check the numbers. Tests: 37 passed.

## 9. Numbers card: only quote these, exactly as written in the results file

Source for all of the below: evals/results/results_aegis-suite-v2_2026-11-13.md

- Suite: aegis-suite-v2, run 2026-11-13, model fake-scripted, browser driver Playwright, Chromium headless.
- Tasks: 30 total, 16 browser, 14 API, 13 with a designed-in mid-task failure.
- Recovery on: 30 of 30, 100.0 percent, mean latency 0.86 seconds per run.
- Recovery off: 17 of 30, 56.7 percent, mean latency 0.50 seconds per run.
- Safety tasks whose correct outcome is a refused run pass in both modes: b07, b09, a04, a06, plus approved variants b08 and a05 pass when approval is granted.
- Tests: 37 passed. Command: make test.

## 10. The honest limit, in one place

- The v2 runs used a scripted, fake planner with a fixed plan per task. File: src/aegis/models/fake.py. Trace events name the model fake-scripted so a test run can never be mistaken for a real result.
- Because of that, the numbers prove the runtime, the permission gates, the browser tool, and the recovery engine. They do not prove live model planning.
- The live model rerun is pending a real Anthropic API key. Adapters for Anthropic, OpenAI, and local endpoints exist in src/aegis/models/http_adapters.py and fail cleanly with a typed error when no key is set.
- Aegis is a new personal build, public on GitHub, not a company production deployment. Production experience is the pipeline and agent work on the resume Jaspar holds.
- If you are asked a number that is not in the results file, say it is not measured yet. That answer is part of the project discipline: if it is not in a trace, it did not happen.
