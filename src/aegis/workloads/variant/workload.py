"""Workload 1: genetic variant evidence curation (v2 of the genetic evidence agent).

Input:  {"variant": "17-43045712-T-C", "phenotype": "...", "use_browser": true}
Output: ACMG-style classification (computed in code), every criterion with
        its source, verified literature citations, a grounded narrative,
        or an explicit decline when evidence is insufficient.

DAG (parallel where the arrows allow):

    plan ─┬─> consequence ─┬─> constraint ──────────────┐
          │                ├─> clinical ─> browser_xcheck┤
          │                └─> literature ──────────────┤
          └─> population ───────────────────────────────┴─> gather_done
                 -> verify -> classify -> report -> final -> reflect
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ...core.engine import Step, StepContext, Workflow
from ...core.errors import PermanentError
from ...core.resilience import NO_RETRY, RetryPolicy
from ...guardrails import injection
from ...llm.base import LLMRequest
from ...tools import bio
from ...tools.registry import Risk, ToolSpec
from . import acmg
from .fixtures import fixtures

# ------------------------------------------------------------------ schemas


class Plan(BaseModel):
    run_literature: bool = True
    run_browser_crosscheck: bool = True
    literature_focus: list[str] = Field(default_factory=list, description="evidence types to prioritise")
    notes: str = ""


class EvidenceItem(BaseModel):
    pmid: str
    evidence_type: str = Field(description="functional | case_report | segregation | population | review | other")
    direction: str = Field(description="pathogenic | benign | unclear")
    quote: str = Field(description="exact sentence copied from the abstract that supports this item")


class EvidenceList(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)


class SupportVerdict(BaseModel):
    supports: bool
    direction: str = Field(description="pathogenic | benign | unclear")
    reason: str = ""


class Report(BaseModel):
    summary: str = Field(description="4-8 sentences for a clinical reviewer, using only the provided evidence")


# ------------------------------------------------------------------- tools


class VariantArg(BaseModel):
    variant: str


class ClinvarArg(BaseModel):
    variant: str
    hgvsc: str | None = None


class GeneArg(BaseModel):
    symbol: str


class PubmedSearchArg(BaseModel):
    gene: str
    protein: str | None = None
    hgvsc: str | None = None


class PmidsArg(BaseModel):
    pmids: list[str]


class UrlArg(BaseModel):
    url: str


def register_tools(svc) -> None:
    offline = svc.options.mode == "offline"
    s = svc.settings
    fx = fixtures(svc.options.seed if offline else 7) if offline else None
    if not offline and "http" not in svc.extras:
        from ...tools.http import Http

        svc.extras["http"] = Http(ncbi_has_key=bool(s.ncbi_key))

    def fixture(vid: str, key: str):
        if vid not in fx:
            raise PermanentError(f"no fixture for {vid}")
        return fx[vid][key]

    async def t_vep(a: VariantArg, ctx):
        return fixture(a.variant, "vep") if offline else await bio.vep(svc.extras["http"], bio.parse_variant(a.variant))

    async def t_gnomad(a: VariantArg, ctx):
        return fixture(a.variant, "gnomad") if offline else await bio.gnomad(svc.extras["http"], bio.parse_variant(a.variant))

    async def t_constraint(a: GeneArg, ctx):
        if offline:
            return next((v["constraint"] for v in fx.values() if v["constraint"]["symbol"] == a.symbol),
                        {"symbol": a.symbol, "loeuf": None})
        return await bio.gnomad_constraint(svc.extras["http"], a.symbol)

    async def t_clinvar(a: ClinvarArg, ctx):
        if offline:
            return fixture(a.variant, "clinvar")
        return await bio.clinvar(svc.extras["http"], bio.parse_variant(a.variant), a.hgvsc, s.ncbi_key, s.ncbi_email)

    async def t_pubmed_search(a: PubmedSearchArg, ctx):
        if offline:
            vid = ctx.services.store.get_run(ctx.run_id)["input"]["variant"]
            return {"source": "pubmed(fixture)", "query": f"{a.gene} {a.protein}",
                    "pmids": [p["pmid"] for p in fixture(vid, "papers")]}
        return await bio.pubmed_search(svc.extras["http"], a.gene, a.protein, a.hgvsc, 12, s.ncbi_key, s.ncbi_email)

    async def t_pubmed_fetch(a: PmidsArg, ctx):
        if offline:
            vid = ctx.services.store.get_run(ctx.run_id)["input"]["variant"]
            return [p for p in fixture(vid, "papers") if p["pmid"] in a.pmids]
        return await bio.pubmed_fetch(svc.extras["http"], a.pmids, s.ncbi_key, s.ncbi_email)

    reg = svc.tools.register
    reg(ToolSpec("vep", "Ensembl VEP consequence, HGVS, REVEL, AlphaMissense", VariantArg, t_vep,
                 roles={"consequence", "baseline"}))
    reg(ToolSpec("gnomad", "gnomAD v4 allele frequency and FAF95", VariantArg, t_gnomad,
                 roles={"population", "baseline"}))
    reg(ToolSpec("gnomad_constraint", "gnomAD gene LoF constraint (LOEUF)", GeneArg, t_constraint,
                 roles={"population", "baseline"}))
    reg(ToolSpec("clinvar", "ClinVar germline classification (comparison only, never evidence)", ClinvarArg,
                 t_clinvar, roles={"clinical", "baseline"}))
    reg(ToolSpec("pubmed_search", "PubMed search for variant mentions", PubmedSearchArg, t_pubmed_search,
                 roles={"literature", "baseline"}))
    reg(ToolSpec("pubmed_fetch", "PubMed abstracts", PmidsArg, t_pubmed_fetch, roles={"literature", "baseline"},
                 untrusted_output=True))


# ------------------------------------------------------------------- steps


def _svc(ctx: StepContext):
    return ctx.services


def _feat(ctx: StepContext, name: str) -> bool:
    return ctx.services.options.feature(name)


async def step_plan(ctx: StepContext) -> dict:
    svc = _svc(ctx)
    v = ctx.run_input["variant"]
    lessons = svc.memory.recall(f"variant evidence {v}", "variant") if svc.memory else []
    lesson_txt = "\n".join(f"- {l['text']}" for l in lessons) or "(none)"
    req = LLMRequest(
        system="You plan evidence gathering for germline variant curation. Decide which optional evidence "
               "sources are worth running. Population frequency and consequence are always run.",
        messages=[{"role": "user", "content":
                   f"Variant (GRCh38): {v}\nPhenotype: {ctx.run_input.get('phenotype') or 'not given'}\n"
                   f"Lessons from past runs:\n{lesson_txt}\nReturn the plan."}],
        tier="fast", purpose="variant_plan", max_tokens=300)
    plan, _ = await svc.router.structured(req, Plan)
    out = plan.model_dump()
    # Code has the last word: the model may add work, never remove required work,
    # and cannot turn on the browser when the operator disabled it.
    out["run_browser_crosscheck"] = bool(out["run_browser_crosscheck"] and svc.options.allow_browser
                                         and ctx.run_input.get("use_browser", True))
    out["lessons_used"] = [l["id"] for l in lessons]
    return out


async def step_consequence(ctx):
    return (await _svc(ctx).tools.call("consequence", "vep", {"variant": ctx.run_input["variant"]}, ctx.step_id)).output


async def step_population(ctx):
    return (await _svc(ctx).tools.call("population", "gnomad", {"variant": ctx.run_input["variant"]}, ctx.step_id)).output


async def step_constraint(ctx):
    gene = (ctx.outputs.get("consequence") or {}).get("gene_symbol")
    if not gene:
        return {"symbol": None, "loeuf": None}
    return (await _svc(ctx).tools.call("population", "gnomad_constraint", {"symbol": gene}, ctx.step_id)).output


async def step_clinical(ctx):
    if _svc(ctx).options.eval_mask_clinvar:
        return {"masked": True, "reason": "ClinVar is the benchmark ground truth"}
    con = ctx.outputs.get("consequence") or {}
    return (await _svc(ctx).tools.call("clinical", "clinvar",
                                       {"variant": ctx.run_input["variant"], "hgvsc": con.get("hgvsc")},
                                       ctx.step_id)).output


async def step_literature(ctx):
    svc = _svc(ctx)
    con = ctx.outputs.get("consequence") or {}
    gene = con.get("gene_symbol")
    if not gene:
        return {"papers": [], "items": [], "note": "no gene symbol"}
    sr = await svc.tools.call("literature", "pubmed_search",
                              {"gene": gene, "protein": con.get("protein_short"), "hgvsc": con.get("hgvsc")}, ctx.step_id)
    pmids = sr.output.get("pmids", [])[:12]
    papers = (await svc.tools.call("literature", "pubmed_fetch", {"pmids": pmids}, ctx.step_id)).output if pmids else []
    if not papers:
        return {"query": sr.output.get("query"), "papers": [], "items": []}

    # RAG: index abstracts, retrieve the passages most likely to hold functional evidence.
    index = svc.extras["make_retriever"]()
    for p in papers:
        index.add(p["pmid"], f"{p['title']}. {p['abstract']}", {"pmid": p["pmid"]}, max_words=120)
    focus = " ".join(ctx.outputs.get("plan", {}).get("literature_focus") or [])
    query = f"{gene} {con.get('protein_short') or ''} functional assay activity loss of function wild-type {focus}"
    ret = index.search(query, k=8)
    passages = []
    for h in ret.hits:
        clean = injection.scan(h.text)
        passages.append(f"[PMID {h.meta['pmid']}] {clean.cleaned}")
    block = injection.spotlight("pubmed", "\n\n".join(passages), 9000)
    req = LLMRequest(
        system=("You extract variant-level evidence from abstracts for ACMG curation. Only report evidence about "
                f"this exact variant ({con.get('protein_short')}). Quote exact sentences. If nothing applies, "
                "return an empty list."),
        messages=[{"role": "user", "content": f"Variant: {gene} {con.get('protein_short')} {con.get('hgvsc')}\n\n{block}"}],
        tier="balanced", purpose="lit_triage", max_tokens=1500)

    k = 3 if _feat(ctx, "self_consistency") else 1
    samples = []
    import asyncio

    async def sample(i):
        r = LLMRequest(**{**req.__dict__, "sample": i, "temperature": 0.7 if k > 1 else 0.0})
        return (await svc.router.structured(r, EvidenceList))[0]

    results = await asyncio.gather(*(sample(i) for i in range(k)), return_exceptions=True)
    samples = [r for r in results if not isinstance(r, BaseException)]
    if not samples:
        raise results[0]
    # Item-level majority vote: keep (pmid, type, direction) seen in >= half the samples.
    from collections import Counter

    keyf = lambda it: (it.pmid, it.evidence_type, it.direction)
    votes = Counter(keyf(it) for s in samples for it in {keyf(x): x for x in s.items}.values())
    need = (len(samples) + 1) // 2
    kept, seen = [], set()
    for s in samples:
        for it in s.items:
            kk = keyf(it)
            if votes[kk] >= need and kk not in seen:
                seen.add(kk)
                kept.append({**it.model_dump(), "votes": votes[kk], "samples": len(samples)})
    return {"query": sr.output.get("query"), "papers": papers, "items": kept,
            "retrieval": {"embedder": ret.embedder, "reranker": ret.reranker,
                          "hits": [{"pmid": h.meta["pmid"], "score": round(h.score, 4)} for h in ret.hits]},
            "injection_flags": sr.injection_rules}


CLINVAR_LABELS = ["Pathogenic/Likely pathogenic", "Likely pathogenic", "Pathogenic", "Uncertain significance",
                  "Likely benign", "Benign/Likely benign", "Benign", "Conflicting classifications of pathogenicity"]


async def step_browser_xcheck(ctx):
    """Open the human-facing ClinVar page in a real browser and check the API agrees."""
    svc = _svc(ctx)
    cv = ctx.outputs.get("clinical") or {}
    if not cv.get("found") or cv.get("masked"):
        return {"skipped": True, "reason": "no ClinVar record to cross-check"}
    if svc.options.mode == "offline":
        return {"skipped": True, "reason": "browser cross-check needs live web; covered by browser tests"}
    from ...tools.browser import BrowserSession

    async with BrowserSession(["ncbi.nlm.nih.gov"], svc.settings.chrome_path, svc.settings.headless) as b:
        async with svc.tracer.span("browser:clinvar_page", "browser", {"url": cv["url"]}):
            obs = await b.goto(cv["url"])
        text = obs.text
    found = next((lab for lab in CLINVAR_LABELS if re.search(r"\b" + re.escape(lab) + r"\b", text)), None)
    method = "regex"
    if found is None:
        class PageLabel(BaseModel):
            classification: str | None

        req = LLMRequest(system="Extract the germline classification shown on this ClinVar page, or null.",
                         messages=[{"role": "user", "content": injection.spotlight(cv["url"], text, 6000)}],
                         tier="fast", purpose="clinvar_page_extract", max_tokens=100)
        found = (await svc.router.structured(req, PageLabel))[0].classification
        method = "llm"
    agrees = acmg.collapse(found) == acmg.collapse(cv.get("classification")) if found else None
    if agrees is False:
        svc.store.add_event(ctx.run_id, "source_disagreement",
                            {"api": cv.get("classification"), "page": found, "url": cv["url"]})
    return {"page_classification": found, "api_classification": cv.get("classification"), "agrees": agrees,
            "method": method}


async def step_gather_done(ctx):
    return {"have": sorted(k for k, v in ctx.outputs.items() if v is not None)}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.%>:-]", " ", s.lower())).strip()


async def step_verify(ctx):
    svc = _svc(ctx)
    lit = ctx.outputs.get("literature") or {}
    papers = {p["pmid"]: p for p in lit.get("papers", [])}
    items = lit.get("items", [])
    if not _feat(ctx, "verifier"):
        return {"functional": [{**it, "verified": it["evidence_type"] == "functional"} for it in items],
                "rejected": [], "checked": False}
    accepted, rejected = [], []
    for it in items:
        p = papers.get(it["pmid"])
        if p is None:
            rejected.append({**it, "why": "PMID not among retrieved papers (hallucinated citation)"})
            svc.store.add_event(ctx.run_id, "citation_rejected", {"pmid": it["pmid"], "why": "unknown pmid"})
            continue
        if _norm(it["quote"])[:400] not in _norm(p["title"] + " " + p["abstract"]):
            rejected.append({**it, "why": "quote not found verbatim in abstract"})
            svc.store.add_event(ctx.run_id, "citation_rejected", {"pmid": it["pmid"], "why": "quote mismatch"})
            continue
        if injection.scan(it["quote"]).flagged:
            rejected.append({**it, "why": "quote is an injected instruction"})
            svc.store.add_event(ctx.run_id, "claim_rejected", {"pmid": it["pmid"], "why": "injection"})
            continue
        accepted.append(it)
    final = []
    if _feat(ctx, "judge_panel") and accepted:
        for it in accepted:
            req = LLMRequest(
                system="You audit evidence for variant curation. Decide whether the quoted sentence, by itself, "
                       "supports the stated evidence direction for this exact variant.",
                messages=[{"role": "user", "content": f"Claim: {it['evidence_type']} evidence, direction "
                                                      f"{it['direction']}, PMID {it['pmid']}\nQuote: \"{it['quote']}\""}],
                tier="judge_a", purpose="support_judge", max_tokens=200)
            verdicts, agree = await svc.router.panel(req, SupportVerdict, ["judge_a", "judge_b"],
                                                     key=lambda v: (v.supports, v.direction))
            ok = agree and all(v.supports and v.direction == it["direction"] for v in verdicts)
            if ok:
                final.append({**it, "verified": it["evidence_type"] == "functional", "judges": len(verdicts)})
            else:
                rejected.append({**it, "why": "judge panel did not agree the quote supports the claim"})
                svc.store.add_event(ctx.run_id, "claim_rejected", {"pmid": it["pmid"], "why": "judges"})
    else:
        final = [{**it, "verified": it["evidence_type"] == "functional"} for it in accepted]
    return {"functional": final, "rejected": rejected, "checked": True}


async def step_classify(ctx):
    ev = {"population": ctx.outputs.get("population"), "consequence": ctx.outputs.get("consequence"),
          "constraint": ctx.outputs.get("constraint"),
          "functional": (ctx.outputs.get("verify") or {}).get("functional", [])}
    if not _feat(ctx, "abstain"):
        res = acmg.classify(ev)
        if res.abstained and res.label == "insufficient_evidence" and ev["population"] and ev["consequence"]:
            res.label, res.abstained = acmg.points_to_label(res.points), False
    else:
        res = acmg.classify(ev)
    d = res.to_dict()
    cv = ctx.outputs.get("clinical") or {}
    if cv.get("found") and not cv.get("masked"):
        d["clinvar_concordant"] = acmg.collapse(cv.get("classification")) == acmg.collapse(res.label)
    return d


_NUM = re.compile(r"(?<![A-Za-z])(\d+(?:\.\d+)?(?:e-?\d+)?)")


def ungrounded_numbers(text: str, evidence_blob: str) -> list[str]:
    nums = set(_NUM.findall(text))
    return sorted(n for n in nums if n not in evidence_blob and n not in {"1", "2", "3", "4", "5"})


async def step_report(ctx):
    import json

    svc = _svc(ctx)
    cls = ctx.outputs["classify"]
    ev = {"variant": ctx.run_input["variant"], "classification": cls,
          "consequence": ctx.outputs.get("consequence"), "population": ctx.outputs.get("population"),
          "functional": (ctx.outputs.get("verify") or {}).get("functional", [])}
    blob = json.dumps(ev, default=str)
    req = LLMRequest(
        system=("Write a short evidence summary for a clinical variant reviewer. Use ONLY facts and numbers present "
                "in the evidence JSON. Cite PMIDs as (PMID:n). If the classification is insufficient_evidence, "
                "say clearly what is missing. Do not change the classification."),
        messages=[{"role": "user", "content": blob[:14000]}], tier="strong", purpose="variant_report", max_tokens=700)
    rep, _ = await svc.router.structured(req, Report)
    text, repaired = rep.summary, False
    bad = ungrounded_numbers(text, blob) if _feat(ctx, "grounding") else []
    if bad:
        svc.store.add_event(ctx.run_id, "claim_rejected", {"why": "ungrounded numbers in report", "numbers": bad})
        req.messages = req.messages + [{"role": "assistant", "content": text},
                                       {"role": "user", "content": f"These numbers are not in the evidence: {bad}. "
                                                                   "Rewrite without them."}]
        rep, _ = await svc.router.structured(req, Report)
        text, repaired = rep.summary, True
        bad = ungrounded_numbers(text, blob)
        if bad:
            for n in bad:
                text = re.sub(rf"(?<![A-Za-z]){re.escape(n)}", "[removed]", text)
    return {"summary": text, "repaired": repaired, "ungrounded_after_repair": bad}


async def step_final(ctx):
    o = ctx.outputs
    lit = o.get("literature") or {}
    out = {
        "variant": ctx.run_input["variant"],
        "gene": (o.get("consequence") or {}).get("gene_symbol"),
        "classification": (o.get("classify") or {}).get("label"),
        "points": (o.get("classify") or {}).get("points"),
        "abstained": (o.get("classify") or {}).get("abstained"),
        "criteria": (o.get("classify") or {}).get("criteria"),
        "report": (o.get("report") or {}).get("summary"),
        "citations": [f"PMID:{f['pmid']}" for f in (o.get("verify") or {}).get("functional", [])],
        "rejected_evidence": len((o.get("verify") or {}).get("rejected", [])),
        "papers_considered": len(lit.get("papers", [])),
        "retrieval": lit.get("retrieval"),
        "clinvar": None if (o.get("clinical") or {}).get("masked") else o.get("clinical"),
        "browser_crosscheck": o.get("browser_xcheck"),
        "degraded_inputs": [k for k in ("constraint", "clinical", "literature", "browser_xcheck") if o.get(k) is None],
    }
    http = ctx.services.extras.get("http")
    if http is not None:
        await http.close()
        ctx.services.extras.pop("http", None)
    return out


async def step_reflect(ctx):
    svc = _svc(ctx)
    if svc.memory is None:
        return {"lessons": []}
    ev = svc.store.events(ctx.run_id)
    interesting = [e for e in ev if e["kind"] in ("tool_retry", "step_attempt_failed", "citation_rejected",
                                                  "claim_rejected", "step_degraded", "provider_fallback",
                                                  "model_escalated", "source_disagreement", "injection_detected")]
    if not interesting:
        return {"lessons": []}
    summary = "\n".join(f"{e['kind']}: {e['data']}" for e in interesting[:60])
    ids = await svc.memory.reflect(svc.router, "variant", ctx.run_id, summary)
    return {"lessons": ids}


# ---------------------------------------------------------------- workflow


def build(run_input: dict) -> Workflow:
    f = run_input.get("_features", {})
    degrade = f.get("degrade", True)
    step_retry = RetryPolicy(max_attempts=3, base_delay=0.3) if f.get("step_retry", True) else NO_RETRY
    plan_ok = lambda key: (lambda out: bool((out.get("plan") or {}).get(key, True)))
    S = lambda *a, **k: Step(*a, retry=k.pop("retry", step_retry), **k)
    steps = [
        S("plan", step_plan),
        S("consequence", step_consequence, ["plan"]),
        S("population", step_population, ["plan"]),
        S("constraint", step_constraint, ["consequence"], optional=degrade),
        S("clinical", step_clinical, ["consequence"], optional=degrade),
        S("literature", step_literature, ["consequence", "plan"], optional=degrade, condition=plan_ok("run_literature"),
          timeout_s=420),
        S("browser_xcheck", step_browser_xcheck, ["clinical", "plan"], optional=True,
          condition=plan_ok("run_browser_crosscheck"), timeout_s=120),
        S("gather_done", step_gather_done, ["consequence", "population", "constraint", "clinical", "literature",
                                            "browser_xcheck"]),
        S("verify", step_verify, ["gather_done"], timeout_s=420),
        S("classify", step_classify, ["verify"]),
        S("report", step_report, ["classify"], optional=degrade),
        S("final", step_final, ["report", "classify"]),
        S("reflect", step_reflect, ["final"], optional=True, retry=NO_RETRY),
    ]
    return Workflow("variant", steps, result_step="final")
