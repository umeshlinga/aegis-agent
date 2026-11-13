"""Real genomics data sources (no API keys required; NCBI key optional).

* Ensembl VEP REST   - consequence, transcript, HGVS, REVEL, AlphaMissense
* gnomAD GraphQL     - population allele frequencies and filtering AF (FAF95)
* NCBI E-utilities   - ClinVar summaries and PubMed abstracts

All coordinates are GRCh38. Input format is gnomAD-style CHROM-POS-REF-ALT
(VCF convention, left-padded indels), e.g. 17-43045712-T-C.

Parsers are defensive: upstream JSON changes over time (ClinVar moved from
`clinical_significance` to `germline_classification` in 2024), and a parser
that crashes on a missing key is the most common silent agent failure.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass

from ..core.errors import MalformedResponse, NotFound, PermanentError
from .http import Http

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
VEP = "https://rest.ensembl.org/vep/human/region"
GNOMAD = "https://gnomad.broadinstitute.org/api"

REFSEQ_CHR = {
    "1": "NC_000001", "2": "NC_000002", "3": "NC_000003", "4": "NC_000004", "5": "NC_000005",
    "6": "NC_000006", "7": "NC_000007", "8": "NC_000008", "9": "NC_000009", "10": "NC_000010",
    "11": "NC_000011", "12": "NC_000012", "13": "NC_000013", "14": "NC_000014", "15": "NC_000015",
    "16": "NC_000016", "17": "NC_000017", "18": "NC_000018", "19": "NC_000019", "20": "NC_000020",
    "21": "NC_000021", "22": "NC_000022", "X": "NC_000023", "Y": "NC_000024",
}

AA3 = {"Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E", "Gly": "G",
       "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P", "Ser": "S",
       "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "Ter": "*"}


@dataclass(frozen=True)
class Variant:
    chrom: str
    pos: int
    ref: str
    alt: str

    @property
    def gnomad_id(self) -> str:
        return f"{self.chrom}-{self.pos}-{self.ref}-{self.alt}"

    def vep_region(self) -> tuple[str, str]:
        r, a, p, c = self.ref, self.alt, self.pos, self.chrom
        if len(r) == 1 and len(a) == 1:
            return f"{c}:{p}-{p}:1", a
        if len(r) > 1 and len(a) == 1 and r[0] == a[0]:  # deletion
            return f"{c}:{p + 1}-{p + len(r) - 1}:1", "-"
        if len(a) > 1 and len(r) == 1 and r[0] == a[0]:  # insertion
            return f"{c}:{p + 1}-{p}:1", a[1:]
        return f"{c}:{p}-{p + len(r) - 1}:1", a

    def spdi_suffix(self) -> str:
        return f":{self.pos - 1}:{self.ref}:{self.alt}"


_VAR = re.compile(r"^(?:chr)?([0-9]{1,2}|X|Y)[-:_](\d+)[-:_]([ACGTN]+)[-:_>/]([ACGTN]+)$", re.I)


def parse_variant(s: str) -> Variant:
    m = _VAR.match(s.strip().replace(" ", ""))
    if not m:
        raise PermanentError(f"cannot parse variant '{s}'; expected CHROM-POS-REF-ALT (GRCh38)")
    chrom = m.group(1).upper()
    return Variant(chrom, int(m.group(2)), m.group(3).upper(), m.group(4).upper())


def protein_short(hgvsp: str | None) -> str | None:
    """'ENSP...:p.Arg1699Trp' -> 'R1699W'; 'p.Gln1756ProfsTer74' -> 'Q1756Pfs'.

    (Test caught the first version turning frameshifts into a missense-looking
    'Q1756P', which would have sent PubMed after the wrong variant.)"""
    if not hgvsp or "p." not in hgvsp:
        return None
    p = hgvsp.split("p.", 1)[1].replace("(", "").replace(")", "")
    m = re.match(r"([A-Z][a-z]{2})(\d+)(.*)$", p)
    if not m or m.group(1) not in AA3:
        return None
    a1, pos, rest = AA3[m.group(1)], m.group(2), m.group(3)
    if "fs" in rest:
        new_aa = AA3.get(rest[:3], "") if rest[:3] in AA3 else ""
        return f"{a1}{pos}{new_aa}fs"
    if rest in ("=", "Ter", "*"):
        return f"{a1}{pos}{'*' if rest != '=' else '='}"
    a2 = AA3.get(rest[:3])
    return f"{a1}{pos}{a2}" if a2 and len(rest) == 3 else None


# ------------------------------------------------------------------ Ensembl VEP
async def vep(http: Http, v: Variant) -> dict:
    region, allele = v.vep_region()
    url = (f"{VEP}/{region}/{allele}?content-type=application/json&canonical=1&mane=1&hgvs=1&numbers=1"
           f"&REVEL=1&AlphaMissense=1")
    data = await http.get_json(url)
    if not isinstance(data, list) or not data:
        raise MalformedResponse("VEP returned no consequences")
    rec = data[0]
    tcs = rec.get("transcript_consequences") or []
    pick = None
    for pref in ("mane_select", "canonical"):
        pick = next((t for t in tcs if t.get(pref)), None)
        if pick:
            break
    pick = pick or (tcs[0] if tcs else {})
    revel = _find_number(pick, ("revel", "revel_score"))
    am = pick.get("alphamissense") or {}
    am_score = am.get("am_pathogenicity") if isinstance(am, dict) else None
    am_class = am.get("am_class") if isinstance(am, dict) else None
    if am_score is None:
        am_score = _find_number(pick, ("am_pathogenicity",))
        am_class = am_class or pick.get("am_class")
    exon = pick.get("exon")  # "23/24"
    return {
        "source": "ensembl_vep",
        "url": url,
        "most_severe_consequence": rec.get("most_severe_consequence"),
        "gene_symbol": pick.get("gene_symbol"),
        "transcript_id": pick.get("transcript_id"),
        "mane_select": pick.get("mane_select"),
        "consequence_terms": pick.get("consequence_terms") or [],
        "hgvsc": pick.get("hgvsc"),
        "hgvsp": pick.get("hgvsp"),
        "protein_short": protein_short(pick.get("hgvsp")),
        "exon": exon,
        "is_last_exon": _is_last(exon),
        "revel": revel,
        "alphamissense": am_score,
        "alphamissense_class": am_class,
    }


def _find_number(d: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.split(",")[0])
            except ValueError:
                pass
    return None


def _is_last(exon: str | None) -> bool | None:
    if not exon or "/" not in exon:
        return None
    a, b = exon.split("/", 1)
    try:
        return int(a.split("-")[-1]) == int(b)
    except ValueError:
        return None


# ---------------------------------------------------------------------- gnomAD
GNOMAD_QUERY = """
query Variant($variantId: String!, $dataset: DatasetId!) {
  variant(variantId: $variantId, dataset: $dataset) {
    variant_id
    genome { ac an af faf95 { popmax popmax_population } }
    exome  { ac an af faf95 { popmax popmax_population } }
  }
}"""


async def gnomad(http: Http, v: Variant, dataset: str = "gnomad_r4") -> dict:
    body = {"query": GNOMAD_QUERY, "variables": {"variantId": v.gnomad_id, "dataset": dataset}}
    data = await http.post_json(GNOMAD, json=body)
    errors = data.get("errors") or []
    if errors and any("not found" in (e.get("message", "").lower()) for e in errors):
        return {"source": "gnomad", "dataset": dataset, "found": False, "ac": 0, "an": None, "af": 0.0,
                "faf95_popmax": None}
    if errors:
        raise MalformedResponse(f"gnomAD error: {errors[0].get('message', '')[:200]}")
    var = (data.get("data") or {}).get("variant")
    if var is None:
        return {"source": "gnomad", "dataset": dataset, "found": False, "ac": 0, "an": None, "af": 0.0,
                "faf95_popmax": None}
    ac = an = 0
    fafs = []
    for part in ("genome", "exome"):
        p = var.get(part) or {}
        ac += p.get("ac") or 0
        an += p.get("an") or 0
        faf = (p.get("faf95") or {}).get("popmax")
        if faf is not None:
            fafs.append((faf, (p.get("faf95") or {}).get("popmax_population"), part))
    best = max(fafs, default=(None, None, None))
    return {"source": "gnomad", "dataset": dataset, "found": True, "ac": ac, "an": an,
            "af": (ac / an) if an else 0.0, "faf95_popmax": best[0], "faf95_population": best[1],
            "faf95_from": best[2]}


# --------------------------------------------------------------------- ClinVar
def _eparams(extra: dict, api_key: str | None, email: str | None) -> dict:
    p = {"tool": "aegis", **extra}
    if api_key:
        p["api_key"] = api_key
    if email:
        p["email"] = email
    return p


async def clinvar(http: Http, v: Variant, hgvsc: str | None = None, api_key: str | None = None,
                  email: str | None = None) -> dict:
    term = f"{v.chrom}[chr] AND {v.pos}:{v.pos + max(len(v.ref), 1) - 1}[chrpos38]"
    es = await http.get_json(f"{EUTILS}/esearch.fcgi",
                             params=_eparams({"db": "clinvar", "term": term, "retmode": "json", "retmax": 50},
                                             api_key, email))
    ids = (es.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return {"source": "clinvar", "found": False, "query": term}
    sm = await http.get_json(f"{EUTILS}/esummary.fcgi",
                             params=_eparams({"db": "clinvar", "id": ",".join(ids), "retmode": "json"}, api_key, email))
    result = sm.get("result") or {}
    want_prefix = REFSEQ_CHR.get(v.chrom, "")
    suffix = v.spdi_suffix()
    candidates = []
    for uid in result.get("uids", []):
        rec = result.get(uid) or {}
        spdis = [vs.get("canonical_spdi", "") for vs in rec.get("variation_set") or []]
        exact = any(s.startswith(want_prefix) and s.endswith(suffix) for s in spdis if s)
        c_match = bool(hgvsc and hgvsc.split(":")[-1] in (rec.get("title") or ""))
        candidates.append((exact, c_match, uid, rec))
    candidates.sort(key=lambda t: (not t[0], not t[1]))
    exact, c_match, uid, rec = candidates[0]
    if not (exact or c_match):
        return {"source": "clinvar", "found": False, "query": term, "ambiguous_ids": [c[2] for c in candidates][:10]}
    gc = rec.get("germline_classification") or rec.get("clinical_significance") or {}
    traits = [t.get("trait_name") for t in (gc.get("trait_set") or rec.get("trait_set") or []) if isinstance(t, dict)]
    return {
        "source": "clinvar",
        "found": True,
        "variation_id": uid,
        "accession": rec.get("accession"),
        "title": rec.get("title"),
        "classification": gc.get("description"),
        "review_status": gc.get("review_status"),
        "last_evaluated": gc.get("last_evaluated"),
        "traits": [t for t in traits if t][:5],
        "match": "spdi" if exact else "hgvsc",
        "url": f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/",
    }


# ---------------------------------------------------------------------- PubMed
async def pubmed_search(http: Http, gene: str, protein: str | None, hgvsc: str | None, retmax: int = 12,
                        api_key: str | None = None, email: str | None = None) -> dict:
    alts = []
    if protein:
        alts.append(f'"{protein}"[tiab]')
    if hgvsc and ":" in hgvsc:
        alts.append(f'"{hgvsc.split(":")[-1]}"[tiab]')
    if not alts:
        return {"source": "pubmed", "query": None, "pmids": []}
    term = f'{gene}[tiab] AND ({" OR ".join(alts)})'
    es = await http.get_json(f"{EUTILS}/esearch.fcgi",
                             params=_eparams({"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax,
                                              "sort": "relevance"}, api_key, email))
    return {"source": "pubmed", "query": term, "pmids": (es.get("esearchresult") or {}).get("idlist") or []}


async def pubmed_fetch(http: Http, pmids: list[str], api_key: str | None = None,
                       email: str | None = None) -> list[dict]:
    if not pmids:
        return []
    xml = await http.get_text(f"{EUTILS}/efetch.fcgi",
                              params=_eparams({"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}, api_key, email))
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise MalformedResponse(f"PubMed XML parse error: {e}") from e
    out = []
    for art in root.findall(".//PubmedArticle"):
        pmid = (art.findtext(".//MedlineCitation/PMID") or "").strip()
        title = "".join(art.find(".//ArticleTitle").itertext()) if art.find(".//ArticleTitle") is not None else ""
        parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            txt = "".join(ab.itertext()).strip()
            parts.append(f"{label}: {txt}" if label else txt)
        year = art.findtext(".//JournalIssue/PubDate/Year") or art.findtext(".//ArticleDate/Year") or ""
        out.append({"pmid": pmid, "title": title.strip(), "abstract": " ".join(parts), "year": year})
    return out


def as_dict(v: Variant) -> dict:
    return asdict(v)


# ------------------------------------------------------- gnomAD gene constraint
GNOMAD_GENE_QUERY = """
query Gene($symbol: String!) {
  gene(gene_symbol: $symbol, reference_genome: GRCh38) {
    symbol
    gnomad_constraint { oe_lof oe_lof_upper pli }
  }
}"""


async def gnomad_constraint(http: Http, symbol: str) -> dict:
    data = await http.post_json(GNOMAD, json={"query": GNOMAD_GENE_QUERY, "variables": {"symbol": symbol}})
    if data.get("errors"):
        raise MalformedResponse(f"gnomAD gene error: {data['errors'][0].get('message', '')[:200]}")
    g = (data.get("data") or {}).get("gene") or {}
    c = g.get("gnomad_constraint") or {}
    return {"source": "gnomad_constraint", "symbol": g.get("symbol", symbol), "loeuf": c.get("oe_lof_upper"),
            "oe_lof": c.get("oe_lof"), "pli": c.get("pli")}
