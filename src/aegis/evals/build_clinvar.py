"""Build the live benchmark from ClinVar's variant_summary with a TEMPORAL split.

Why temporal: a random split leaks. Variants evaluated after the cutoff
were classified with knowledge the system's sources might not have had,
and models may have seen older ClinVar in training. Splitting by
LastEvaluated date (train <= cutoff < test) is the honest test, and it is
the split described on the resume ("held-out ClinVar temporal split").

Filters: GRCh38, short variants with VCF alleles, review status of 2+ stars
(multiple submitters no conflicts, expert panel, practice guideline),
classification in {P, LP, B, LB, VUS} (conflicting excluded). Stratified
by class with a per-gene cap for diversity.

Source: https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz
(several hundred MB; downloaded once to data/).
"""
from __future__ import annotations

import csv
import gzip
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz"
GOOD_REVIEW = {"criteria provided, multiple submitters, no conflicts", "reviewed by expert panel", "practice guideline"}
KEEP = {"Pathogenic": "P/LP", "Likely pathogenic": "P/LP", "Pathogenic/Likely pathogenic": "P/LP",
        "Benign": "B/LB", "Likely benign": "B/LB", "Benign/Likely benign": "B/LB", "Uncertain significance": "VUS"}


def download(dest: str = "data/variant_summary.txt.gz") -> str:
    import httpx

    p = Path(dest)
    if p.exists():
        return str(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", URL, timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        with p.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    return str(p)


def _date(s: str) -> datetime | None:
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def build(src: str, out: str, cutoff: str = "2024-06-30", n_per_class: int = 40, train_per_class: int = 40,
          per_gene_cap: int = 3, seed: int = 13) -> dict:
    cut = datetime.strptime(cutoff, "%Y-%m-%d")
    pools: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with gzip.open(src, "rt", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        for row in rd:
            if row.get("Assembly") != "GRCh38" or row.get("ReviewStatus") not in GOOD_REVIEW:
                continue
            label = KEEP.get(row.get("ClinicalSignificance", ""))
            ref, alt, pos = row.get("ReferenceAlleleVCF"), row.get("AlternateAlleleVCF"), row.get("PositionVCF")
            if not label or not ref or not alt or ref == "na" or alt == "na" or not pos or pos == "-1":
                continue
            if len(ref) > 20 or len(alt) > 20:
                continue
            d = _date(row.get("LastEvaluated", ""))
            if d is None:
                continue
            split = "test" if d > cut else "train"
            pools[(split, label)].append({
                "variant": f"{row['Chromosome']}-{pos}-{ref}-{alt}", "gene": row.get("GeneSymbol"),
                "clinvar_label": row.get("ClinicalSignificance"), "truth": label, "review_status": row["ReviewStatus"],
                "last_evaluated": d.strftime("%Y-%m-%d"), "variation_id": row.get("VariationID"), "split": split,
                "name": row.get("Name", "")[:120],
            })
    rng = random.Random(seed)
    chosen = []
    for (split, label), rows in sorted(pools.items()):
        rng.shuffle(rows)
        per_gene: Counter = Counter()
        want = n_per_class if split == "test" else train_per_class
        for r in rows:
            if per_gene[r["gene"]] >= per_gene_cap:
                continue
            per_gene[r["gene"]] += 1
            r["case_id"] = f"cv{r['variation_id']}"
            chosen.append(r)
            if sum(1 for c in chosen if c["split"] == split and c["truth"] == label) >= want:
                break
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        for r in chosen:
            f.write(json.dumps(r) + "\n")
    return {"cases": len(chosen), "by_split_class": Counter(f"{c['split']}:{c['truth']}" for c in chosen),
            "cutoff": cutoff, "pool_sizes": {f"{k[0]}:{k[1]}": len(v) for k, v in pools.items()}}
