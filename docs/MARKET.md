# Market: what exists, what fails, and where Aegis fits

Researched 13 Nov 2026. Sources are linked; re-check before using numbers in a pitch.

## The problem labs actually have

* Interpretation, not sequencing, is the bottleneck. Interpretation is labour-intensive and done by PhD analysts and genetic counsellors ([AIVA preprint](https://www.researchsquare.com/article/rs-10286684/v1)). One institution's new variants needing curation rose from 1.72 to 3.73 per sample over six years ([BMC Med Genomics 2022](https://link.springer.com/content/pdf/10.1186/s12920-022-01214-y.pdf)).
* A full manual case review takes ~90 minutes in one rare-disease lab; reviewing automated, evidenced classifications took 5–8 minutes ([Congenica poster](https://blog.congenica.com/hubfs/H%20Savage%20e-Poster%20P15-004-D-1.pdf)).
* VUS remain ~35–37% of variants in rare disease and cancer ([Wilk et al. 2026 preprint](https://www.preprints.org/manuscript/202603.1025/v1)).
* Classifications change. Baylor showed that triggering reanalysis from external database updates is efficient and catches up- and downgrades ([Genet Med Open 2024](https://rcastoragev2.blob.core.windows.net/337b1e216b843c493c681b91195eed9e/main.PMC11613846.pdf)).

## Why "just ask an LLM" fails

* Standalone LLMs fabricate variant–disease links, cite non-existent papers, and apply ACMG criteria to made-up evidence ([AIVA preprint](https://www.researchsquare.com/article/rs-10286684/v1)).
* Measured citation fabrication ranges from ~5% to over 90% by model and task; one controlled study found 18% of GPT-4's citations fabricated ([IntuitionLabs review](https://intuitionlabs.ai/articles/citation-reliability-ai-literature-reviews)).
* Multi-model consensus raises citation accuracy from 16.5% to 95.6%, and repetition across runs to 88.9% ([arXiv 2603.03299](https://arxiv.org/pdf/2603.03299)). Aegis already uses both ideas (self-consistency votes, cross-family judges) and adds hard verification against the source text.

## Who is already here (be honest: the space is crowded)

| Category | Examples | What they do well |
|---|---|---|
| Commercial tertiary-analysis platforms | Franklin (QIAGEN), Emedgene (Illumina), QCI Interpret, VarSome Clinical, SeqOne, CentoCloud, Fabric, Congenica | Prioritisation, automated ACMG, integrated databases. In one 24-variant comparison Franklin classified 75% correctly, VarSome Clinical 67%, QCI 63% ([ALMED 2025](https://www.citedrive.com/en/discovery/evaluating-seven-bioinformatics-platforms-for-tertiary-analysis-of-genomic-data-from-whole-exome-sequencing-in-a-pilot-group-of-patients/)). Emedgene reports 2–5× efficiency ([listing](https://intuitionlabs.ai/software/emerging-specialized-niche-categories/genomic-data-interpretation-and-variant-classification/emedgene)). |
| Rule engines | InterVar, TAPES, Genebe | Transparent ACMG automation ([Bioinformatics benchmark](https://www.dent.chula.ac.th/publications/10-1093-bioinformatics-btaf623/)) |
| ML classifiers | DiagAI (trained on 2.5M ClinVar variants) | Fast class prediction ([medRxiv 2025](https://medrxiv.org/content/medrxiv/early/2025/02/06/2025.02.04.25321641.full.pdf)) |
| Agentic LLM research (2026) | AIVA (VCF-level agent, exposed as a tool to Claude/Codex), VarLitAgent + VarLitBench (functional-evidence curation, ICML 2026), AIVARI Agent, ARGUS (regulatory variants, planner + deterministic verifier, abstains), DeepBD, MARRVEL-MCP (AJHG 2026), VarChat | Grounded agents. VarLitBench found evidence direction (PS3 vs BS3) is easy for models but evidence *strength* remains hard ([ICML 2026](https://icml.cc/virtual/2026/70819)). |

**Plain conclusion:** "an evidence-grounded LLM agent for variant interpretation" is not a new idea in 2026. Several academic groups and every major vendor are on it. Claiming otherwise would be wrong and would be caught.

## Where Aegis can still be different (hypotheses to test, not facts)

1. **Measured reliability as the product.** Others publish accuracy. Aegis can publish *failure behaviour*: recovery under upstream faults, crash safety, verified-citation rate, abstention curve, all reproducible from a seeded harness. Labs under CAP/CLIA care about validation and audit; a tool whose failure modes are measured is easier to validate.
2. **Reanalysis as a service.** The watchlist re-curates a lab's past calls and flags what changed (new papers, ClinVar changes, points crossing a class boundary), with before/after reports. The Baylor result says this is valuable; most tools are built around new cases, not old calls.
3. **Hard verification instead of "trust the RAG".** Quotes must exist verbatim; the class is computed in code; literature strength is capped; numbers in prose must be in the evidence. That's an auditor-friendly contract a reviewer can check in minutes.
4. **Open and self-hostable.** Patient data never has to leave the lab; models can be local.

## Who would pay (to validate by talking to people, before writing more code)

* Small and mid-size clinical labs and hospital genetics units without enterprise budgets.
* Genetic-counselling groups who need reanalysis of old reports.
* Research cores and biobanks returning results.

## What would make it a company (in order)

1. Live benchmark numbers (ClinVar temporal split) and the LLM-vs-Aegis comparison on real variants.
2. Ten conversations with variant scientists and lab directors: what takes them longest, what would they trust, would they pay for reanalysis alerts.
3. One design partner running the watchlist on a de-identified set of their past calls.
4. Then: Postgres + multi-user auth, gene-specific VCEP specifications, more ACMG criteria, PMC full text, SOC 2 / HIPAA posture.

Not a medical device today; positioned as research use only, like most tools above.
