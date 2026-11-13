"""Workload 2: account research for outbound sales (the job of an AI BDR).

Input:  {"start_url": "https://example.com", "domain": "example.com",
         "draft_outreach": true, "sender": {"name": "...", "company": "...", "offer": "..."}}
Output: a sourced account brief (every fact carries a URL and an exact quote
        that was verified against the captured page text), talking points,
        and optionally an outreach email that only leaves via a human-approved,
        exactly-once send tool.

DAG:  browse -> extract (hybrid RAG + LLM) -> verify (code + judge panel)
             -> brief -> outreach (HIGH risk, approval) -> final
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from ...agents.browser_agent import BrowserAgent
from ...core.engine import Step, StepContext, Workflow
from ...core.resilience import NO_RETRY, RetryPolicy
from ...guardrails import injection, pii
from ...llm.base import LLMRequest
from ...tools.browser import BrowserSession
from ...tools.registry import Risk, ToolSpec

FIELDS = ["what_they_do", "products", "target_customers", "pricing_model", "headquarters", "team_or_size_signals",
          "recent_news", "hiring_signals", "tech_or_integrations"]


class Fact(BaseModel):
    field: str = Field(description="one of: " + ", ".join(FIELDS))
    value: str = Field(description="concise fact, <= 30 words")
    source_url: str
    quote: str = Field(description="exact sentence copied from that page supporting the value")


class FactList(BaseModel):
    facts: list[Fact] = Field(default_factory=list)


class PagePick(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=6)


class Brief(BaseModel):
    one_liner: str
    summary: str = Field(description="5-7 sentences")
    talking_points: list[str] = Field(default_factory=list, max_length=4)
    open_questions: list[str] = Field(default_factory=list, max_length=4)


class Email(BaseModel):
    subject: str
    body: str = Field(description="<= 120 words, plain text, no claims beyond the verified facts")


class SupportVerdict(BaseModel):
    supports: bool
    reason: str = ""


class CitedSentence(BaseModel):
    text: str
    fact_ids: list[int] = Field(default_factory=list, description="ids of the verified facts this sentence relies on")


class Opener(BaseModel):
    subject: str
    sentences: list[CitedSentence] = Field(default_factory=list, max_length=6)


class SendEmailArg(BaseModel):
    to_domain: str
    subject: str
    body: str


def register_tools(svc) -> None:
    async def send_email(a: SendEmailArg, ctx):
        # Never actually emails anyone: writes to an outbox file. The approval
        # gate and the exactly-once ledger are the parts being demonstrated.
        async def do():
            out = Path(svc.settings.db_path).parent / "outbox"
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{ctx.run_id}.eml"
            path.write_text(f"To: (contact at {a.to_domain})\nSubject: {a.subject}\n\n{a.body}\n")
            return {"queued": True, "path": str(path)}

        return await _effect(ctx, f"send_email:{a.to_domain}", do)

    svc.tools.register(ToolSpec("send_email", "Queue an outreach email (requires human approval)", SendEmailArg,
                                send_email, risk=Risk.HIGH, roles={"outreach"}, idempotent=False))


async def _effect(tool_ctx, key, fn):
    store = tool_ctx.services.store
    idem = f"{tool_ctx.run_id}:{key}"
    prior = store.get_effect(idem)
    if prior is not None:
        store.add_event(tool_ctx.run_id, "effect_deduplicated", {"key": key})
        return prior
    res = await fn()
    store.put_effect(idem, tool_ctx.run_id, res)
    return res


def _domain(ri: dict) -> str:
    return ri.get("domain") or (urlparse(ri["start_url"]).hostname or "")


async def step_browse(ctx: StepContext) -> dict:
    svc = ctx.services
    ri = ctx.run_input
    domain = _domain(ri)
    agent = BrowserAgent(svc.router, svc.tracer, svc.store, ctx.run_id, tier="fast", max_steps=ri.get("max_steps", 10),
                         use_vision_fallback=svc.options.mode != "offline")
    goal = ("Collect pages that describe what this company does, its products, customers, pricing, team, news and "
            "careers. Extract each useful page, then finish.")
    async with BrowserSession([domain], svc.settings.chrome_path, svc.settings.headless,
                              ladder=svc.options.feature("browser_ladder")) as session:
        res = await agent.run(session, goal, ri["start_url"])
        seen = {p["url"].rstrip("/") for p in res.pages}
        # Second pass: let the model pick unvisited same-domain links worth reading.
        obs = await session.goto(ri["start_url"])
        links = sorted({urljoin(obs.url, e.href) for e in obs.elements if e.href and not e.href.startswith(("#", "mailto:", "javascript:"))})
        links = [u for u in links if session.domain_ok(u) and u.rstrip("/") not in seen][:40]
        if links:
            req = LLMRequest(system="Pick up to 4 URLs most likely to describe the company's business, customers, "
                                    "pricing, news or hiring.",
                             messages=[{"role": "user", "content": "\n".join(links)}], tier="fast",
                             purpose="pick_pages", max_tokens=300)
            pick, _ = await svc.router.structured(req, PagePick)
            for u in [u for u in pick.urls if u in links][:4]:
                try:
                    o = await session.goto(u)
                    res.pages.append({"url": o.url, "title": o.title, "text": injection.scan(o.text).cleaned})
                except Exception as e:  # one bad page must not sink the run
                    svc.store.add_event(ctx.run_id, "page_skipped", {"url": u, "error": type(e).__name__})
        stats = session.stats
    if not res.pages:
        from ...core.errors import BrowserNoEffect

        raise BrowserNoEffect(f"no pages captured ({res.stop_reason})")
    return {"pages": res.pages, "actions": res.actions, "stop_reason": res.stop_reason,
            "recoveries": res.recoveries, "injection_flags": res.injection_flags,
            "browser_stats": {"actions": stats.actions, "verified_first_try": stats.verified_first_try,
                              "recovered": stats.recovered, "failed": stats.failed, "by_strategy": stats.by_strategy}}


async def step_extract(ctx: StepContext) -> dict:
    svc = ctx.services
    pages = ctx.outputs["browse"]["pages"]
    index = svc.extras["make_retriever"]()
    for i, p in enumerate(pages):
        index.add(f"page{i}", p["text"], {"url": p["url"]}, max_words=140)
    passages, used = [], set()
    for f in FIELDS:
        for h in index.search(f.replace("_", " "), k=3).hits:
            key = (h.doc_id, h.chunk_id)
            if key not in used:
                used.add(key)
                passages.append(f"[URL {h.meta['url']}]\n{h.text}")
    block = injection.spotlight("company_site", "\n\n".join(passages), 14000)
    req = LLMRequest(system=("Extract facts about the company for a sales brief. Use only the passages. Each fact must "
                             "cite the passage URL and quote one exact sentence from it. Skip fields with no evidence."),
                     messages=[{"role": "user", "content": block}], tier="balanced", purpose="company_extract",
                     max_tokens=2000)
    facts, _ = await svc.router.structured(req, FactList)
    return {"facts": [f.model_dump() for f in facts.facts if f.field in FIELDS], "passages": len(passages),
            "embedder": index.embedder.name}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip(" .")


async def step_verify(ctx: StepContext) -> dict:
    svc = ctx.services
    pages = {p["url"]: p["text"] for p in ctx.outputs["browse"]["pages"]}
    norm_pages = {u: _norm(t) for u, t in pages.items()}
    ok, rejected = [], []
    for f in ctx.outputs["extract"]["facts"]:
        if not svc.options.feature("verifier"):
            ok.append({**f, "verified": False})
            continue
        src = norm_pages.get(f["source_url"]) or norm_pages.get(f["source_url"].rstrip("/")) \
            or norm_pages.get(f["source_url"] + "/")
        if src is None:
            rejected.append({**f, "why": "source URL was never visited"})
            svc.store.add_event(ctx.run_id, "citation_rejected", {"url": f["source_url"], "why": "unvisited url"})
            continue
        if _norm(f["quote"]) not in src:
            rejected.append({**f, "why": "quote not found on page"})
            svc.store.add_event(ctx.run_id, "citation_rejected", {"url": f["source_url"], "why": "quote mismatch"})
            continue
        ok.append({**f, "verified": True})
    if svc.options.feature("judge_panel") and ok and svc.options.feature("verifier"):
        kept = []
        for f in ok:
            req = LLMRequest(system="Does the quote, by itself, support the stated fact? Be strict.",
                             messages=[{"role": "user", "content": f"Fact ({f['field']}): {f['value']}\nQuote: \"{f['quote']}\""}],
                             tier="judge_a", purpose="fact_judge", max_tokens=150)
            verdicts, agree = await svc.router.panel(req, SupportVerdict, ["judge_a", "judge_b"], key=lambda v: v.supports)
            if agree and all(v.supports for v in verdicts):
                kept.append(f)
            else:
                rejected.append({**f, "why": "judges did not agree it is supported"})
                svc.store.add_event(ctx.run_id, "claim_rejected", {"field": f["field"], "why": "judges"})
        ok = kept
    return {"facts": ok, "rejected": rejected}


async def step_brief(ctx: StepContext) -> dict:
    svc = ctx.services
    facts = ctx.outputs["verify"]["facts"]
    sender = ctx.run_input.get("sender") or {}
    req = LLMRequest(system=("Write an account brief for a sales rep using ONLY these verified facts. Talking points "
                             "should connect the facts to the sender's offer. List what is still unknown."),
                     messages=[{"role": "user", "content": json.dumps({"facts": facts, "sender": sender})[:12000]}],
                     tier="strong", purpose="company_brief", max_tokens=900)
    brief, _ = await svc.router.structured(req, Brief)
    return brief.model_dump()


_SPECIFIC = re.compile(r"\d|\b(you|your|team|recently|launched|announced|customers|raised|hiring|based in|headquartered)\b", re.I)


async def step_opener(ctx: StepContext) -> dict:
    """Grounded opener: every sentence that says something specific about the
    prospect must cite verified facts by id. Code removes sentences that make
    specific claims without a citation, and citations to facts that don't exist.
    This is the 'no claim without a source' rule, enforced outside the model."""
    svc = ctx.services
    facts = ctx.outputs["verify"]["facts"]
    sender = ctx.run_input.get("sender") or {}
    numbered = [{"id": i, "field": f["field"], "value": f["value"]} for i, f in enumerate(facts)]
    req = LLMRequest(system=("Write a short, specific, honest first-touch sales email (3-5 sentences). Every sentence "
                             "that states something about the prospect must cite the ids of the verified facts it "
                             "uses. Do not state anything about the prospect that is not in a fact. No invented "
                             "metrics, no fake familiarity."),
                     messages=[{"role": "user", "content": json.dumps({"verified_facts": numbered, "sender": sender})}],
                     tier="balanced", purpose="grounded_opener", max_tokens=600)
    op, _ = await svc.router.structured(req, Opener)
    kept, dropped = [], []
    valid = set(range(len(facts)))
    for snt in op.sentences:
        ids = [i for i in snt.fact_ids if i in valid]
        if _SPECIFIC.search(snt.text) and not ids:
            dropped.append({"text": snt.text, "why": "specific claim without a verified fact"})
            continue
        kept.append({"text": snt.text, "facts": ids})
    if dropped:
        svc.store.add_event(ctx.run_id, "claim_rejected", {"why": "uncited sentence in opener", "n": len(dropped)})
    body = " ".join(k["text"] for k in kept)
    return {"subject": op.subject, "body": body, "sentences": kept, "dropped": dropped,
            "sources": sorted({facts[i]["source_url"] for k in kept for i in k["facts"]})}


async def step_outreach(ctx: StepContext) -> dict:
    svc = ctx.services
    op = ctx.outputs["opener"]
    email = Email(subject=op["subject"], body=op["body"] or "(no verified facts to personalise with)")
    body, red = pii.redact(email.body)
    # HIGH risk: parks the run for a human unless approved.
    res = await svc.tools.call("outreach", "send_email",
                               {"to_domain": _domain(ctx.run_input), "subject": email.subject, "body": body},
                               ctx.step_id)
    return {"subject": email.subject, "body": body, "pii_redactions": red, "send": res.output}


async def step_final(ctx: StepContext) -> dict:
    o = ctx.outputs
    return {"domain": _domain(ctx.run_input), "brief": o.get("brief"), "facts": o["verify"]["facts"],
            "rejected_facts": len(o["verify"]["rejected"]), "pages": [p["url"] for p in o["browse"]["pages"]],
            "browser": {k: o["browse"][k] for k in ("browser_stats", "recoveries", "injection_flags", "stop_reason")},
            "opener": o.get("opener"), "outreach": o.get("outreach")}


def build(run_input: dict) -> Workflow:
    f = run_input.get("_features", {})
    rp = RetryPolicy(max_attempts=3, base_delay=0.5) if f.get("step_retry", True) else NO_RETRY
    steps = [
        Step("browse", step_browse, retry=rp, timeout_s=600),
        Step("extract", step_extract, ["browse"], retry=rp),
        Step("verify", step_verify, ["extract"], retry=rp),
        Step("brief", step_brief, ["verify"], retry=rp),
        Step("opener", step_opener, ["verify"], retry=rp, optional=True),
        Step("outreach", step_outreach, ["brief", "opener"], retry=rp, optional=True,
             condition=lambda out: bool(run_input.get("draft_outreach")) and bool(out.get("opener"))),
        Step("final", step_final, ["brief", "opener", "outreach"], retry=rp),
    ]
    return Workflow("company", steps, result_step="final")
