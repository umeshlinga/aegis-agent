# 4-minute walkthrough (record with Loom)

Before recording: `aegis doctor` all OK; run one variant and one company run so caches are warm; `aegis serve` open in a browser; terminal font large.

**0:00–0:20 What it is.** "This is Aegis, v2 of the genetic evidence agent on my resume. It's a runtime that keeps multi-step agents correct when things break. It's not in production; everything you'll see runs live against public APIs and Claude."

**0:20–1:10 A live variant run.** `aegis variant <variant> --phenotype "..."`. Switch to the explorer: point at the parallel bars (VEP, gnomAD, PubMed at once), the three triage samples, the two judges from different model families, the browser opening ClinVar. Show the criteria table: "the model gathered evidence; the class is computed in code, and literature evidence is capped."

**1:10–1:50 Crash and resume.** Start another run, `kill -9` it mid-way, `aegis runs` shows it stuck in running, `aegis resume <id>`. Explorer: `steps_recovered_after_crash`; completed steps were not redone; cost is still counted.

**1:50–2:40 Browser agent and safety.** `aegis company <site> --outreach`. Show recoveries in the timeline (`wait_settle`, `js_click`), an `injection_detected` event if the site has one, or the test site's pricing page. The run parks on the email: approve it in the explorer; it resumes and sends exactly once (show the outbox file and the single row in effects).

**2:40–3:30 How I measured it.** Open `docs/results/.../report.html`: baseline vs Aegis under seeded faults (18% → 100% under harsh, 0% → 100% under outage, 52% → 100% under crash), paired p-values, ablations showing which mechanism earns which number. "These are offline numbers on synthetic data; they measure the runtime. Live ClinVar numbers are in progress / here."

**3:30–4:00 A failure I fixed.** Open FAILURES.md F2 (or a live L-entry): symptom in the trace, root cause, fix, 59/60 → 60/60.
