# Recorded data fixtures

Every file in this directory is a recorded copy of a real response
from a real public API, captured on 2026-11-13. Tests replay these
files through stub transports so the suite never touches the network.
They are fixtures for the tests only. The product code in
`src/aegis/data/` always talks to the live APIs; nothing in the
product path reads these files.

| File | Source | Query that produced it |
| --- | --- | --- |
| `pubmed_esearch_brca1_variant.json` | PubMed E-utilities esearch | `db=pubmed`, term `BRCA1 variant`, retmax 2 |
| `pubmed_efetch_43015897.xml` | PubMed E-utilities efetch | `db=pubmed`, id `43015897` (the first id from the esearch above) |
| `clinvar_esearch_brca1_gene.json` | ClinVar E-utilities esearch | `db=clinvar`, term `BRCA1[gene]`, retmax 2 |
| `clinvar_esummary_5042465.json` | ClinVar E-utilities esummary | `db=clinvar`, id `5042465` (the first id from the esearch above) |
| `gnomad_variant_1_55516888_g_ga_r2_1.json` | gnomAD GraphQL API | variant `1-55516888-G-GA`, dataset `gnomad_r2_1` |
| `gnomad_variant_not_found_r4.json` | gnomAD GraphQL API | variant `1-55516888-G-GA`, dataset `gnomad_r4` (a real not-found answer: the variant is in r2_1 and not in r4) |
