"""ACMG/AMP evidence scoring, in code.

The model gathers and triages evidence. It never assigns the final class.
Criteria strengths and the combining rule are deterministic, so the same
evidence always gives the same answer, and every point is auditable.

Combining uses the ClinGen SVI Bayesian points system (Tavtigian et al.,
Hum Mutat 2020): supporting 1, moderate 2, strong 4, very strong 8;
benign criteria subtract. >=10 P, 6-9 LP, 0-5 VUS, -1..-6 LB, <=-7 B.
BA1 is stand-alone benign.

Implemented subset (documented limitation, see docs/LIMITATIONS.md):
  BA1, BS1, PM2_Supporting      population frequency (gnomAD v4 FAF95 popmax)
  PVS1 (simplified decision tree, Abou Tayoun 2018 shape)
  PP3 / BP4                      REVEL with ClinGen calibrated thresholds (Pejaver 2022)
  PS3 / BS3                      functional evidence from literature, CAPPED at
                                 Supporting unless a human approves a higher strength
ClinVar assertions (PP5/BP6) are NOT used: ClinGen retired them, and in the
benchmark ClinVar is the ground truth, so using it would be circular.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

STRENGTH_POINTS = {"supporting": 1, "moderate": 2, "strong": 4, "very_strong": 8}

LOF_TERMS = {"stop_gained", "frameshift_variant", "splice_donor_variant", "splice_acceptor_variant", "start_lost"}

# Genes where loss of function is an established disease mechanism (curated
# starter list; gnomAD LOEUF < 0.35 is also accepted as LoF intolerance).
LOF_GENES = {
    "BRCA1", "BRCA2", "PALB2", "ATM", "MLH1", "MSH2", "MSH6", "PMS2", "APC", "TP53", "PTEN", "STK11", "CDH1",
    "NF1", "NF2", "RB1", "VHL", "SMAD4", "BMPR1A", "TSC1", "TSC2", "MYBPC3", "LDLR", "DMD", "CFTR", "BRIP1",
    "RAD51C", "RAD51D", "SCN1A", "KMT2D", "CHD7", "ARID1B", "ANKRD11", "SYNGAP1", "MECP2", "FBN1", "COL3A1",
}

REVEL_PP3 = [(0.932, "strong"), (0.773, "moderate"), (0.644, "supporting")]
REVEL_BP4 = [(0.016, "strong"), (0.183, "moderate"), (0.290, "supporting")]


@dataclass
class Criterion:
    code: str
    strength: str
    direction: str  # pathogenic | benign
    points: int
    rationale: str
    sources: list[str] = field(default_factory=list)


@dataclass
class Classification:
    label: str  # Pathogenic | Likely pathogenic | VUS | Likely benign | Benign | insufficient_evidence
    points: int
    criteria: list[Criterion]
    abstained: bool = False
    abstain_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["criteria"] = [asdict(c) for c in self.criteria]
        return d


@dataclass
class Thresholds:
    ba1: float = 0.05
    bs1: float = 0.01  # gene-specific in practice; conservative generic default
    pm2_max_ac: int = 0  # absent from gnomAD (ClinGen SVI: PM2 at Supporting)
    loeuf_lof: float = 0.35
    functional_cap: str = "supporting"


def _crit(code, strength, direction, rationale, sources) -> Criterion:
    pts = STRENGTH_POINTS[strength] * (1 if direction == "pathogenic" else -1)
    return Criterion(code if strength == _default_strength(code) else f"{code}_{strength.title().replace('_', '')}",
                     strength, direction, pts, rationale, sources)


def _default_strength(code: str) -> str:
    return {"BA1": "very_strong", "PVS1": "very_strong", "PS3": "strong", "BS3": "strong", "BS1": "strong", "PM2": "moderate",
            "PP3": "supporting", "BP4": "supporting"}.get(code, "supporting")


def classify(evidence: dict, t: Thresholds | None = None, approved_functional_strength: str | None = None) -> Classification:
    """evidence = {"population": {...}|None, "consequence": {...}|None,
                   "constraint": {...}|None, "functional": [verified items]}"""
    t = t or Thresholds()
    crits: list[Criterion] = []
    pop = evidence.get("population")
    con = evidence.get("consequence")
    cons = evidence.get("constraint") or {}

    if pop is None or con is None:
        missing = [n for n, v in (("population", pop), ("consequence", con)) if v is None]
        return Classification("insufficient_evidence", 0, [], True, f"required evidence unavailable: {missing}")

    # ---- population
    faf = pop.get("faf95_popmax")
    if pop.get("found") and faf is not None and faf > t.ba1:
        c = _crit("BA1", "very_strong", "benign", f"FAF95 popmax {faf:.4g} > {t.ba1}", ["gnomad"])
        c.points = -99
        return Classification("Benign", c.points, [c])
    if pop.get("found") and faf is not None and faf > t.bs1:
        crits.append(_crit("BS1", "strong", "benign", f"FAF95 popmax {faf:.4g} > {t.bs1}", ["gnomad"]))
    elif not pop.get("found") or (pop.get("ac") or 0) <= t.pm2_max_ac:
        crits.append(_crit("PM2", "supporting", "pathogenic", "absent from gnomAD v4", ["gnomad"]))

    # ---- null variant
    terms = set(con.get("consequence_terms") or [])
    lof_hit = terms & LOF_TERMS
    gene = (con.get("gene_symbol") or "").upper()
    loeuf = cons.get("loeuf")
    lof_gene = gene in LOF_GENES or (loeuf is not None and loeuf < t.loeuf_lof)
    if lof_hit and lof_gene:
        if {"stop_gained", "frameshift_variant"} & lof_hit:
            if con.get("is_last_exon"):
                s, why = "moderate", "predicted to escape NMD (last exon)"
            else:
                s, why = "very_strong", "predicted NMD"
        elif {"splice_donor_variant", "splice_acceptor_variant"} & lof_hit:
            s, why = "strong", "canonical splice site"
        else:
            s, why = "moderate", "start loss"
        src = "curated LoF gene list" if gene in LOF_GENES else f"gnomAD LOEUF {loeuf}"
        crits.append(_crit("PVS1", s, "pathogenic", f"{','.join(sorted(lof_hit))} in {gene}; {why}; {src}",
                           ["vep", "gnomad_constraint"]))

    # ---- computational (missense)
    revel = con.get("revel")
    if revel is not None and not lof_hit:
        for thr, s in REVEL_PP3:
            if revel >= thr:
                crits.append(_crit("PP3", s, "pathogenic", f"REVEL {revel:.3f} >= {thr}", ["vep:revel"]))
                break
        else:
            for thr, s in REVEL_BP4:
                if revel <= thr:
                    crits.append(_crit("BP4", s, "benign", f"REVEL {revel:.3f} <= {thr}", ["vep:revel"]))
                    break

    # ---- functional literature (model-triaged, verifier-checked, code-capped)
    func = [f for f in evidence.get("functional") or [] if f.get("verified")]
    cap = approved_functional_strength or t.functional_cap
    dmg = [f for f in func if f.get("direction") == "pathogenic"]
    ben = [f for f in func if f.get("direction") == "benign"]
    if dmg and not ben:
        crits.append(_crit("PS3", cap, "pathogenic",
                           f"{len(dmg)} verified functional report(s) showing damaging effect (strength capped)",
                           [f"PMID:{f['pmid']}" for f in dmg]))
    elif ben and not dmg:
        crits.append(_crit("BS3", cap, "benign",
                           f"{len(ben)} verified functional report(s) showing no effect (strength capped)",
                           [f"PMID:{f['pmid']}" for f in ben]))

    points = sum(c.points for c in crits)
    label = points_to_label(points)
    # Decline instead of guessing: a VUS supported by a single weak criterion
    # and nothing else is reported as insufficient evidence.
    if label == "VUS" and len(crits) <= 1 and not func:
        return Classification("insufficient_evidence", points, crits, True,
                              "fewer than two independent criteria and no functional evidence")
    return Classification(label, points, crits)


def points_to_label(p: int) -> str:
    if p >= 10:
        return "Pathogenic"
    if p >= 6:
        return "Likely pathogenic"
    if p >= 0:
        return "VUS"
    if p >= -6:
        return "Likely benign"
    return "Benign"


def collapse(label: str | None) -> str | None:
    """5-class -> 3-class for benchmarking: P/LP, VUS, B/LB."""
    if not label:
        return None
    l = label.lower()
    if "conflicting" in l:
        return None
    if "pathogenic" in l and "benign" not in l:
        return "P/LP"
    if "benign" in l and "pathogenic" not in l:
        return "B/LB"
    if "uncertain" in l or l == "vus":
        return "VUS"
    return None
