"""Workload registry and the deterministic offline handlers.

Offline handlers stand in for the LLM when running without API keys (CI,
tests, fault-injection studies). They are plain code and deliberately
imperfect: they emit hallucinated citations and ungrounded numbers at a
configurable rate (AEGIS_OFFLINE_HALLUCINATION, default 0.2) so the
verifier and grounding checks have something real to catch. Numbers from
offline runs describe the runtime, not model quality.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from types import SimpleNamespace

from .company import workload as company
from .llm_only import workload as llm_only
from .variant import workload as variant

WORKLOADS = {
    "variant": SimpleNamespace(build=variant.build, register_tools=variant.register_tools),
    "company": SimpleNamespace(build=company.build, register_tools=company.register_tools),
    "llm_only": SimpleNamespace(build=llm_only.build, register_tools=llm_only.register_tools),
}


def _rate() -> float:
    return float(os.environ.get("AEGIS_OFFLINE_HALLUCINATION", "0.2"))


def _roll(*parts) -> float:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _content(req) -> str:
    return req.messages[-1]["content"] if req.messages else ""


DMG = re.compile(r"abolish|reduced protein function|lost in cells|loss of function|less than 10%", re.I)
NEU = re.compile(r"comparable to wild-type|retained normal function|no effect", re.I)


def h_variant_plan(req):
    return json.dumps({"run_literature": True, "run_browser_crosscheck": True,
                       "literature_focus": ["functional"], "notes": "offline plan"})


def h_lit_triage(req):
    text = req.messages[0]["content"]
    items = []
    for m in re.finditer(r"\[PMID (\d+)\] (.*?)(?=\n\n\[PMID|\n</untrusted_data>|$)", text, re.S):
        pmid, body = m.group(1), m.group(2)
        for sent in re.split(r"(?<=\.)\s+", body):
            d = "pathogenic" if DMG.search(sent) else "benign" if NEU.search(sent) else None
            if d:
                items.append({"pmid": pmid, "evidence_type": "functional", "direction": d, "quote": sent.strip()})
    r = _roll(text[:200], req.sample)
    if items and r < _rate() / 2:
        items.append({**items[0], "pmid": "99999999"})  # hallucinated citation
    elif items and r < _rate():
        items[0] = {**items[0], "quote": items[0]["quote"].replace("activity", "function entirely")}  # paraphrase
    return json.dumps({"items": items})


def h_support_judge(req):
    c = _content(req)
    claim_dir = "pathogenic" if "direction pathogenic" in c else "benign" if "direction benign" in c else "unclear"
    q = c.split("Quote:", 1)[-1]
    d = "pathogenic" if DMG.search(q) else "benign" if NEU.search(q) else "unclear"
    return json.dumps({"supports": d == claim_dir and d != "unclear", "direction": d, "reason": "offline keyword judge"})


def h_variant_report(req):
    c = _content(req)
    try:
        ev = json.loads(c if c.strip().startswith("{") else c[c.find("{"):])
    except Exception:
        ev = {}
    cls = ev.get("classification", {})
    s = (f"Variant {ev.get('variant')} is classified as {cls.get('label')} with {cls.get('points')} points. "
         f"Criteria applied: {', '.join(x['code'] for x in cls.get('criteria', [])) or 'none'}.")
    if len(req.messages) == 1 and _roll(c[:300]) < _rate():
        s += " It has been reported in 17 unrelated families."  # ungrounded number
    return json.dumps({"summary": s})


def h_reflect(req):
    c = _content(req)
    lessons = []
    if "tool_retry" in c:
        lessons.append({"text": "Expect transient upstream failures; keep retries with backoff on data tools.",
                        "applies_to": "data tools"})
    if "citation_rejected" in c:
        lessons.append({"text": "Literature triage produced unverifiable quotes; require verbatim sentences.",
                        "applies_to": "lit_triage"})
    return json.dumps({"lessons": lessons})


def h_null_page(req):
    return json.dumps({"classification": None})


def h_vision(req):
    return json.dumps({"found": False, "x": 0, "y": 0})


KEYWORDS = ("about", "product", "pricing", "customer", "news", "career", "team", "blog", "platform")


def h_browser_action(req):
    c = _content(req)
    url = re.search(r"^URL: (.*)$", c, re.M).group(1).strip()
    hist = c.split("ACTIONS SO FAR:", 1)[1].split("CURRENT PAGE:", 1)[0]
    extracted = set(re.findall(r"extract .*?@ (\S+)", hist))
    clicked = set(t.strip().lower() for t in re.findall(r"\d+: click (.*?) -> ", hist))
    failed = set(t.strip().lower() for t in re.findall(r"\d+: click (.*?) -> (?:FAILED|REFUSED)", hist))
    if url not in extracted:
        return json.dumps({"kind": "extract", "reason": "capture page"})
    for m in re.finditer(r"^\[(\d+)\] (\w+)(?:/\w+)? \| (.*?) \| (.*)$", c, re.M):
        eid, tag, text, href = int(m.group(1)), m.group(2), m.group(3).strip(), m.group(4).strip()
        t = text.lower()
        if any(k in t for k in KEYWORDS) and t not in clicked and t not in failed:
            return json.dumps({"kind": "click", "target_id": eid, "reason": f"open {text}"})
        if "subscribe" in t and t not in failed and "probe-risky" in c:
            return json.dumps({"kind": "click", "target_id": eid, "reason": "probe"})
    return json.dumps({"kind": "done", "reason": "covered key pages"})


def h_pick_pages(req):
    links = _content(req).splitlines()
    pick = [u for u in links if any(k in u.lower() for k in KEYWORDS)][:2]
    return json.dumps({"urls": pick})


FIELD_RULES = [
    ("what_they_do", re.compile(r"\b(we build|we help|platform that|is a company)\b", re.I)),
    ("pricing_model", re.compile(r"\b(per month|per seat|pricing|plan starts)\b", re.I)),
    ("headquarters", re.compile(r"\b(headquartered|based in)\b", re.I)),
    ("hiring_signals", re.compile(r"\b(hiring|open roles|join our team)\b", re.I)),
    ("target_customers", re.compile(r"\b(customers|trusted by|used by)\b", re.I)),
    ("recent_news", re.compile(r"\b(announced|launched|raised)\b", re.I)),
    ("tech_or_integrations", re.compile(r"\b(integrates|integration|api)\b", re.I)),
]


def h_company_extract(req):
    c = _content(req)
    facts, seen = [], set()
    for m in re.finditer(r"\[URL (\S+)\]\n(.*?)(?=\n\n\[URL|\n</untrusted_data>|$)", c, re.S):
        url, body = m.group(1), m.group(2)
        for sent in re.split(r"(?<=[.!?])\s+", body):
            for field, pat in FIELD_RULES:
                if pat.search(sent) and field not in seen:
                    seen.add(field)
                    facts.append({"field": field, "value": sent.strip()[:150], "source_url": url, "quote": sent.strip()})
    if facts and _roll(c[:300]) < _rate():
        facts[0] = {**facts[0], "quote": facts[0]["quote"] + " (industry leader)"}
    return json.dumps({"facts": facts})


def h_fact_judge(req):
    return json.dumps({"supports": True, "reason": "offline judge"})


def h_company_brief(req):
    d = json.loads(_content(req))
    facts = d.get("facts", [])
    by = {f["field"]: f["value"] for f in facts}
    return json.dumps({"one_liner": by.get("what_they_do", "Company with limited public information."),
                       "summary": " ".join(f"{k}: {v}" for k, v in by.items())[:900] or "No verified facts.",
                       "talking_points": [f"Ask about {k.replace('_', ' ')}" for k in list(by)[:3]],
                       "open_questions": [f for f in ("pricing_model", "team_or_size_signals") if f not in by]})


def h_company_email(req):
    d = json.loads(_content(req))
    return json.dumps({"subject": "Quick question",
                       "body": f"Hi, I read that {d['brief']['one_liner'][:100]} Would a short call make sense? "
                               f"Reach me at sales@example.org."})


def h_raw_llm(req):
    """Offline stand-in for 'ask the model directly': confident, sometimes wrong,
    and cites a mix of real fixture PMIDs and invented ones, which is the
    pattern the live comparison is designed to measure."""
    c = _content(req)
    v = re.search(r"variant (\S+)", c).group(1)
    from .variant.fixtures import fixtures

    d = fixtures(0).get(v) or {}
    r = _roll(v)
    truth = d.get("truth", "VUS")
    guess = truth if r < 0.6 else ("P/LP" if truth != "P/LP" else "B/LB")
    label = {"P/LP": "Likely pathogenic", "B/LB": "Likely benign", "VUS": "VUS"}[guess]
    real = [p["pmid"] for p in d.get("papers", [])][:1]
    fake = [str(31000000 + int(r * 1e6))] if r < 0.7 else []
    cites = [{"pmid": p, "claim": "functional study"} for p in real + fake]
    return json.dumps({"classification": label, "confidence": 0.9, "gene": (d.get("vep") or {}).get("gene_symbol"),
                       "protein_change": (d.get("vep") or {}).get("protein_short"), "citations": cites,
                       "reasoning": "offline stand-in"})


def register_offline_handlers(p) -> None:
    p.register("raw_llm_classify", h_raw_llm)
    for name, fn in {
        "variant_plan": h_variant_plan, "lit_triage": h_lit_triage, "support_judge": h_support_judge,
        "variant_report": h_variant_report, "reflect": h_reflect, "clinvar_page_extract": h_null_page,
        "vision_locate": h_vision, "browser_action": h_browser_action, "pick_pages": h_pick_pages,
        "company_extract": h_company_extract, "fact_judge": h_fact_judge, "company_brief": h_company_brief,
        "company_email": h_company_email,
    }.items():
        p.register(name, fn)
