"""Comparison arm: what people do today, a strong LLM asked directly.

One call to the strongest model, no tools, no retrieval: "classify this
variant and cite PubMed". Then Aegis checks the answer the same way a
reviewer would: does each PMID exist, and does the paper mention this gene
or protein change at all? This arm measures the problem Aegis exists to fix.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ...core.engine import Step, StepContext, Workflow
from ...core.resilience import RetryPolicy
from ...llm.base import LLMRequest
from ..variant import acmg


class Cited(BaseModel):
    pmid: str
    claim: str = Field(description="what this paper shows about the variant")


class RawAnswer(BaseModel):
    classification: str = Field(description="Pathogenic | Likely pathogenic | VUS | Likely benign | Benign")
    confidence: float = Field(ge=0, le=1)
    gene: str | None = None
    protein_change: str | None = None
    citations: list[Cited] = Field(default_factory=list)
    reasoning: str = ""


PROMPT = ("Classify the germline variant {v} (GRCh38) using ACMG/AMP criteria. Give the gene and protein change, "
          "a classification (Pathogenic, Likely pathogenic, VUS, Likely benign, Benign), your confidence, and the "
          "PubMed IDs (PMIDs) of papers that support your call, each with what the paper shows.")


async def step_answer(ctx: StepContext) -> dict:
    svc = ctx.services
    req = LLMRequest(system="You are an expert clinical molecular geneticist.",
                     messages=[{"role": "user", "content": PROMPT.format(v=ctx.run_input["variant"])}],
                     tier="strong", purpose="raw_llm_classify", max_tokens=1200)
    ans, resp = await svc.router.structured(req, RawAnswer)
    return {**ans.model_dump(), "model": resp.model}


async def step_check(ctx: StepContext) -> dict:
    """Fetch every cited PMID from PubMed and check it exists and is on-topic."""
    svc = ctx.services
    ans = ctx.outputs["answer"]
    pmids = [re.sub(r"\D", "", c["pmid"]) for c in ans.get("citations", [])]
    pmids = [p for p in pmids if p][:15]
    papers = []
    if pmids:
        papers = (await svc.tools.call("literature", "pubmed_fetch", {"pmids": pmids}, ctx.step_id)).output
    found = {p["pmid"]: p for p in papers}
    gene = (ans.get("gene") or "").upper()
    prot = (ans.get("protein_change") or "").replace("p.", "")
    checks = []
    for p in pmids:
        doc = found.get(p)
        text = ((doc or {}).get("title", "") + " " + (doc or {}).get("abstract", "")).upper()
        checks.append({"pmid": p, "exists": doc is not None,
                       "mentions_gene": bool(doc and gene and gene in text),
                       "mentions_change": bool(doc and prot and prot.upper() in text),
                       "title": (doc or {}).get("title")})
    return {"checks": checks}


async def step_final(ctx: StepContext) -> dict:
    ans, chk = ctx.outputs["answer"], ctx.outputs["check"]["checks"]
    return {
        "variant": ctx.run_input["variant"], "classification": ans.get("classification"),
        "pred3": acmg.collapse(ans.get("classification")), "confidence": ans.get("confidence"),
        "gene": ans.get("gene"), "protein_change": ans.get("protein_change"), "model": ans.get("model"),
        "citations_total": len(chk), "citations_nonexistent": sum(not c["exists"] for c in chk),
        "citations_off_topic": sum(c["exists"] and not c["mentions_gene"] for c in chk),
        "citation_checks": chk, "reasoning": ans.get("reasoning", "")[:1500],
    }


def build(run_input: dict) -> Workflow:
    rp = RetryPolicy(max_attempts=3, base_delay=0.5)
    return Workflow("llm_only", [
        Step("answer", step_answer, retry=rp),
        Step("check", step_check, ["answer"], retry=rp),
        Step("final", step_final, ["answer", "check"], retry=rp),
    ], result_step="final")


def register_tools(svc) -> None:
    # Reuse the variant workload's PubMed tools (same rate limits, same fault path).
    from ..variant.workload import register_tools as reg

    reg(svc)
