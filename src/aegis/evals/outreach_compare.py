"""Verified outreach vs plain-LLM outreach, on real company websites.

    aegis outreach-compare --sites examples/sites.txt --n 20

For each site:
  naive   homepage text -> strong model -> "personalised" opener (today's common practice)
  aegis   browse the site, extract facts with quotes, verify them, write an opener
          where every specific sentence cites a verified fact (code drops the rest)

Both emails are then scored by the SAME independent checker:
  1. a model splits the email into atomic claims about the prospect
  2. each claim is matched against everything captured from the site (hybrid retrieval)
  3. a judge from a different tier decides supported / unsupported against that evidence
So the baseline is judged against more of the site than it ever saw, which favours it.

Outputs: per-site side-by-side with every claim marked, summary metrics with
Wilson intervals, and a paired test on "email contains an unsupported claim".
"""
from __future__ import annotations

import asyncio
import html
import json
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..llm.base import LLMRequest
from ..rag.index import HybridIndex, make_embedder
from ..services import RunOptions, Runtime
from .metrics import mcnemar, wilson


class Claims(BaseModel):
    claims: list[str] = Field(default_factory=list, description="atomic factual claims about the prospect company")


class Verdict(BaseModel):
    supported: bool
    reason: str = ""


def load_sites(path: str) -> list[str]:
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line if line.startswith("http") else f"https://{line}")
    return out


async def check_email(router, email: str, pages: list[dict], embedder_kind: str = "auto") -> dict:
    """Independent claim checker used for BOTH arms."""
    if not email.strip():
        return {"claims": [], "supported": 0, "unsupported": 0}
    req = LLMRequest(system=("List every factual claim this sales email makes about the RECIPIENT'S company "
                             "(what they do, products, customers, numbers, news, hiring, location). Ignore claims "
                             "about the sender and pleasantries. One short claim per item."),
                     messages=[{"role": "user", "content": email}], tier="fast", purpose="claim_extract", max_tokens=600)
    claims, _ = await router.structured(req, Claims)
    idx = HybridIndex(make_embedder(embedder_kind))
    for i, p in enumerate(pages):
        idx.add(f"p{i}", p.get("text", ""), {"url": p.get("url")}, max_words=120)
    rows = []
    for c in claims.claims[:10]:
        hits = idx.search(c, k=4).hits
        evidence = "\n\n".join(f"[{h.meta.get('url')}] {h.text}" for h in hits)[:6000]
        jreq = LLMRequest(system=("Decide strictly whether the evidence from the company's own website supports the "
                                  "claim. Numbers, names and dates must match. If the evidence does not say it, it is "
                                  "unsupported."),
                          messages=[{"role": "user", "content": f"CLAIM: {c}\n\nEVIDENCE:\n{evidence or '(none)'}"}],
                          tier="judge_b", purpose="claim_judge", max_tokens=200)
        v, _ = await router.structured(jreq, Verdict)
        rows.append({"claim": c, "supported": v.supported, "reason": v.reason[:200],
                     "evidence_url": hits[0].meta.get("url") if hits else None})
    return {"claims": rows, "supported": sum(r["supported"] for r in rows),
            "unsupported": sum(not r["supported"] for r in rows)}


async def run_outreach_compare(sites: list[str], settings, mode: str, out_dir: str, concurrency: int = 2,
                               sender: dict | None = None) -> dict:
    from ..core.store import Store

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = Store(str(out / "outreach.db"))
    rt = Runtime(settings, RunOptions(mode=mode, approval_mode="auto_reject"), store=store)
    sender = sender or {"name": "Umesh", "company": "Aegis", "offer": "verified, auditable outbound research"}
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(i: int, url: str) -> dict:
        domain = (urlparse(url).hostname or "").removeprefix("www.")
        row = {"site": url, "domain": domain}
        inp = {"start_url": url, "domain": domain, "draft_outreach": False, "sender": sender, "max_steps": 8}
        async with sem:
            for arm, wl in (("aegis", "company"), ("naive", "outreach_naive")):
                rid = f"{arm}-{i:03d}-{domain}"[:60]
                t0 = time.perf_counter()
                try:
                    try:
                        if store.get_run(rid)["status"] != "succeeded":
                            await rt.resume(rid)
                    except KeyError:
                        await rt.run(wl, inp, run_id=rid)
                except Exception as e:
                    row[f"{arm}_error"] = f"{type(e).__name__}: {e}"[:200]
                r = store.get_run(rid) if _exists(store, rid) else {"status": "failed", "result": None}
                res = r.get("result") or {}
                row[f"{arm}_status"] = r["status"]
                row[f"{arm}_secs"] = round(time.perf_counter() - t0, 1)
                row[f"{arm}_cost"] = (res.get("usage") or {}).get("cost_usd") or 0.0
                o = res.get("output") or {}
                if arm == "aegis":
                    op = o.get("opener") or {}
                    row.update(aegis_subject=op.get("subject"), aegis_email=op.get("body", ""),
                               aegis_dropped=len(op.get("dropped") or []), aegis_sources=op.get("sources", []),
                               aegis_facts=len(o.get("facts") or []), aegis_rejected_facts=o.get("rejected_facts", 0),
                               pages=(store.get_steps(rid).get("browse") or {}).get("output", {}).get("pages", []))
                else:
                    row.update(naive_subject=o.get("subject"), naive_email=o.get("body", ""))
            pages = row.get("pages") or []
            if not pages and _exists(store, f"naive-{i:03d}-{domain}"[:60]):
                f = (store.get_steps(f"naive-{i:03d}-{domain}"[:60]).get("fetch") or {}).get("output")
                pages = [f] if f else []
            checker_rid = f"check-{i:03d}-{domain}"[:60]
            if not _exists(store, checker_rid):
                store.create_run("outreach_naive", {"checker": True}, checker_rid)
            svc = rt.make_services(checker_rid, {})
            for arm in ("naive", "aegis"):
                email = row.get(f"{arm}_email") or ""
                row[f"{arm}_check"] = await check_email(svc.router, email, pages,
                                                        "hashing" if rt.options.mode == "offline" else settings.embedder)
            row.pop("pages", None)
            row["pages_captured"] = len(pages)
            return row

    for coro in asyncio.as_completed([one(i, s) for i, s in enumerate(sites)]):
        rows.append(await coro)
    rows.sort(key=lambda r: r["site"])
    (out / "rows.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    summary = summarize(rows, mode)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out / "outreach_compare.html").write_text(render(summary, rows, mode))
    return summary


def _exists(store, rid: str) -> bool:
    try:
        store.get_run(rid)
        return True
    except KeyError:
        return False


def summarize(rows: list[dict], mode: str) -> dict:
    def arm(a: str) -> dict:
        ok = [r for r in rows if r.get(f"{a}_status") == "succeeded" and r.get(f"{a}_email")]
        claims = sum(len(r[f"{a}_check"]["claims"]) for r in ok)
        unsup = sum(r[f"{a}_check"]["unsupported"] for r in ok)
        bad = sum(1 for r in ok if r[f"{a}_check"]["unsupported"] > 0)
        w = wilson(bad, len(ok)) if ok else (None, None, None)
        return {"emails": len(ok), "claims": claims, "unsupported_claims": unsup,
                "unsupported_claim_rate": unsup / claims if claims else None,
                "emails_with_unsupported_claim": bad, "email_failure_rate": w[0], "email_failure_ci": [w[1], w[2]],
                "specific_claims_per_email": claims / len(ok) if ok else None,
                "mean_cost_usd": statistics.mean([r.get(f"{a}_cost") or 0 for r in rows]) if rows else None,
                "median_secs": statistics.median([r.get(f"{a}_secs") or 0 for r in rows]) if rows else None}

    both = [r for r in rows if r.get("naive_email") and r.get("aegis_email")]
    paired = mcnemar([r["naive_check"]["unsupported"] == 0 for r in both],
                     [r["aegis_check"]["unsupported"] == 0 for r in both]) if both else {}
    return {"mode": mode, "sites": len(rows), "naive": arm("naive"), "aegis": arm("aegis"),
            "paired_clean_email": paired, "date": time.strftime("%Y-%m-%d")}


def render(s: dict, rows: list[dict], mode: str) -> str:
    e = lambda x: html.escape("" if x is None else str(x))
    pct = lambda x: "–" if x is None else f"{x:.0%}"
    N, A = s["naive"], s["aegis"]
    banner = ("<p class=warn>OFFLINE demo: a local test site and scripted stand-in models. Run with your key on real "
              "company sites for real numbers.</p>") if mode == "offline" else ""

    def bar(label, a, b, fmt=pct, lower=False):
        mx = max([x for x in (a, b) if isinstance(x, (int, float))] + [1e-9])
        w = lambda x: 0 if not isinstance(x, (int, float)) else max(x / mx * 240, 2)
        return (f"<tr><td>{label}{' <span class=muted>(lower is better)</span>' if lower else ''}</td>"
                f"<td><span class=b style='width:{w(a):.0f}px;background:#eb6834'></span> {fmt(a)}</td>"
                f"<td><span class=b style='width:{w(b):.0f}px;background:#2a78d6'></span> {fmt(b)}</td></tr>")

    table = "".join([
        bar("Emails with at least one unsupported claim", N["email_failure_rate"], A["email_failure_rate"], lower=True),
        bar("Claims not supported by the company's own site", N["unsupported_claim_rate"], A["unsupported_claim_rate"], lower=True),
        bar("Specific claims per email", N["specific_claims_per_email"], A["specific_claims_per_email"],
            fmt=lambda x: f"{x:.1f}" if x is not None else "–"),
        bar("Cost per email (USD)", N["mean_cost_usd"], A["mean_cost_usd"], fmt=lambda x: f"${x:.3f}" if x is not None else "–", lower=True),
        bar("Median time per company (s)", N["median_secs"], A["median_secs"], fmt=lambda x: f"{x:.0f}s" if x is not None else "–", lower=True),
    ])

    def claims_html(chk):
        return "".join(f"<li class='{'ok' if c['supported'] else 'bad'}'>{'✓' if c['supported'] else '✗'} {e(c['claim'])}"
                       f"<div class=muted>{e(c['reason'])}</div></li>" for c in chk.get("claims", [])) or "<li class=muted>no claims</li>"

    body = "".join(
        f"<h3>{e(r['domain'])} <span class=muted>({r.get('pages_captured')} pages captured)</span></h3><div class=pair>"
        f"<div><div class=lab>Plain LLM</div><p>{e(r.get('naive_email') or r.get('naive_error'))}</p><ul>{claims_html(r.get('naive_check', {}))}</ul></div>"
        f"<div><div class=lab>Aegis (verified)</div><p>{e(r.get('aegis_email') or r.get('aegis_error'))}</p><ul>{claims_html(r.get('aegis_check', {}))}</ul>"
        f"<div class=muted>{e(r.get('aegis_facts'))} verified facts · {e(r.get('aegis_rejected_facts'))} facts rejected · {e(r.get('aegis_dropped'))} uncited sentences dropped</div></div></div>"
        for r in rows)
    p = s.get("paired_clean_email") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Verified outreach vs plain LLM</title><style>
body{{font:14px/1.5 -apple-system,Segoe UI,Inter,sans-serif;max-width:1100px;margin:26px auto;padding:0 18px;color:#151a22}}
h1{{font-size:22px}}h3{{font-size:15px;margin:22px 0 6px;border-top:1px solid #e3e6eb;padding-top:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:6px 8px;border-bottom:1px solid #eceef2;text-align:left}}
th{{font-size:11.5px;color:#5d6675;text-transform:uppercase}}.b{{display:inline-block;height:12px;border-radius:3px;vertical-align:middle}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.lab{{font-size:12px;color:#5d6675;text-transform:uppercase}}
ul{{padding-left:18px;font-size:13px}}.ok{{color:#1d6b43}}.bad{{color:#a32222}}.muted{{color:#5d6675;font-size:12px}}
.warn{{background:#fff7e6;border:1px solid #f0dcae;border-radius:8px;padding:8px 12px}}
@media(max-width:800px){{.pair{{grid-template-columns:1fr}}}}</style></head><body>
<h1>Verified outreach vs a plain LLM</h1>
<p class=muted>{s['sites']} companies · {e(s['date'])} · both emails scored by the same independent claim checker against everything captured from each company's own website</p>{banner}
<p><span class=b style="width:12px;background:#eb6834"></span> plain LLM (homepage → strong model) &nbsp; <span class=b style="width:12px;background:#2a78d6"></span> Aegis (browse → verify → cite)</p>
<table><tr><th>Measure</th><th>Plain LLM</th><th>Aegis</th></tr>{table}</table>
<p class=muted>Plain LLM: {e(N['unsupported_claims'])} of {e(N['claims'])} claims unsupported; Aegis: {e(A['unsupported_claims'])} of {e(A['claims'])}.
Paired test on clean emails (exact McNemar): Aegis-only clean {e(p.get('only_b_ok'))}, plain-only clean {e(p.get('only_a_ok'))}, p = {e(round(p['p_value'], 4) if p.get('p_value') is not None else None)}.</p>
{body}</body></html>"""
