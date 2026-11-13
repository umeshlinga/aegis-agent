# Limitations (stated up front)

* **Not in production.** No external users. It is a complete, tested system run against live public APIs.
* **ACMG subset.** BA1, BS1 (generic 1% threshold; real thresholds are gene-specific), PM2_Supporting, simplified PVS1 (no exon-level NMD nuance beyond last exon, no alternative transcripts), PP3/BP4 from REVEL only, PS3/BS3 capped at Supporting. Not implemented: PS1/PM5, PM1, PM4/BP3, PS2/PM6, PP1/BS4, PS4, PM3, BP7 (needs SpliceAI). Research use only; not for clinical decisions.
* **Literature is abstracts only.** Full text (PMC) would find more functional evidence and more noise.
* **ClinVar indel matching** uses SPDI first and HGVS c. second; left/right normalization differences can miss matches (reported as not found, never as a wrong match).
* **Injection detection is pattern-based** and can be evaded; the guarantees come from permissions and approvals.
* **Single-node durability.** SQLite and in-process scheduling; multiple workers need Postgres, leases and a queue (ADR-002).
* **Offline eval measures the runtime only.** Synthetic fixtures, deterministic stand-in model.
* **Browser agent** handles same-origin research; it does not log in, solve CAPTCHAs, or fill forms by design.
* **Vision fallback** returns coordinates from a screenshot; it is the last rung and is not yet evaluated separately on real sites.
* **Prices** in `llm/base.py` are list prices as of November 2026; check before quoting costs.
