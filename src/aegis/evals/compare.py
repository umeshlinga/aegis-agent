"""Head-to-head: a frontier LLM asked directly vs Aegis, on the same variants.

    aegis compare --cases evals/data/clinvar_benchmark.jsonl --n 30
    aegis compare --variants 17-43045712-T-C 13-32340300-GT-G --truth "P/LP,P/LP"

For each variant:
  llm_only  strongest model, no tools: classification, confidence, cited PMIDs.
            Every PMID is fetched from PubMed: does it exist? does it mention the gene?
  aegis     the full pipeline with ClinVar hidden (ClinVar is the answer key).

Scored against ClinVar expert/multi-submitter classifications. Note the bias
in the LLM's favour: it may have memorised ClinVar during training, which is
why the benchmark uses variants classified after a cutoff date.
"""
from __future__ import annotations

import asyncio
import html
import json
import statistics
import time
from pathlib import Path

from ..services import RunOptions, Runtime
from ..workloads.variant import acmg
from .harness import Case
from .metrics import mcnemar, wilson


async def run_compare(cases: list[Case], settings, mode: str, out_dir: str, concurrency: int = 3) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    from ..core.store import Store

    store = Store(str(out / "compare.db"))
    rt_llm = Runtime(settings, RunOptions(mode=mode, approval_mode="auto_reject", allow_browser=False), store=store)
    rt_aegis = Runtime(settings, RunOptions(mode=mode, approval_mode="auto_reject", allow_browser=False,
                                            eval_mask_clinvar=True), store=store)
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(c: Case) -> dict:
        async with sem:
            res = {"case_id": c.case_id, "variant": c.variant, "truth": c.truth}
            for arm, rt, wl in (("llm_only", rt_llm, "llm_only"), ("aegis", rt_aegis, "variant")):
                rid = f"{arm}-{c.case_id}"
                t0 = time.perf_counter()
                try:
                    try:
                        st = store.get_run(rid)["status"]
                        if st != "succeeded":
                            await rt.resume(rid)
                    except KeyError:
                        await rt.run(wl, {"variant": c.variant, "use_browser": False}, run_id=rid)
                except Exception as e:
                    res[f"{arm}_error"] = f"{type(e).__name__}: {e}"[:200]
                run = store.get_run(rid) if _exists(store, rid) else {"status": "failed", "result": None}
                o = ((run.get("result") or {}).get("output")) or {}
                u = ((run.get("result") or {}).get("usage")) or {}
                res[f"{arm}_status"] = run["status"]
                res[f"{arm}_secs"] = round(time.perf_counter() - t0, 2)
                res[f"{arm}_cost"] = u.get("cost_usd") or 0.0
                res[f"{arm}_run"] = rid
                if arm == "llm_only":
                    res.update(llm_label=o.get("classification"), llm_pred=o.get("pred3"), llm_conf=o.get("confidence"),
                               llm_cites=o.get("citations_total", 0), llm_fake=o.get("citations_nonexistent", 0),
                               llm_offtopic=o.get("citations_off_topic", 0), llm_checks=o.get("citation_checks", []))
                else:
                    lab = o.get("classification")
                    res.update(aegis_label=lab,
                               aegis_pred=None if lab in (None, "insufficient_evidence") else acmg.collapse(lab),
                               aegis_declined=lab == "insufficient_evidence", aegis_points=o.get("points"),
                               aegis_criteria=" ".join(x["code"] for x in o.get("criteria") or []),
                               aegis_cites=len(o.get("citations") or []), aegis_rejected=o.get("rejected_evidence", 0))
            return res

    for coro in asyncio.as_completed([one(c) for c in cases]):
        rows.append(await coro)
    rows.sort(key=lambda r: r["case_id"])
    (out / "rows.jsonl").write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    summary = summarize(rows, mode)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    (out / "compare.html").write_text(render(summary, rows, mode))
    return summary


def _exists(store, rid) -> bool:
    try:
        store.get_run(rid)
        return True
    except KeyError:
        return False


def summarize(rows: list[dict], mode: str) -> dict:
    n = len(rows)

    def arm(prefix: str, pred: str) -> dict:
        ok = [r for r in rows if r.get(f"{prefix}_status") == "succeeded"]
        answered = [r for r in ok if r.get(pred)]
        correct = [r for r in answered if r[pred] == r["truth"]]
        acc = wilson(len(correct), len(answered)) if answered else (None, None, None)
        return {"runs_ok": len(ok), "answered": len(answered), "declined": len(ok) - len(answered),
                "accuracy_answered": acc[0], "accuracy_ci": [acc[1], acc[2]],
                "correct_over_all": len(correct) / n if n else None,
                "mean_cost_usd": statistics.mean([r.get(f"{prefix}_cost") or 0 for r in rows]) if rows else None,
                "median_secs": statistics.median([r.get(f"{prefix}_secs") or 0 for r in rows]) if rows else None}

    llm = arm("llm_only", "llm_pred")
    ae = arm("aegis", "aegis_pred")
    cites = sum(r.get("llm_cites", 0) for r in rows)
    fake = sum(r.get("llm_fake", 0) for r in rows)
    off = sum(r.get("llm_offtopic", 0) for r in rows)
    llm.update(citations=cites, citations_nonexistent=fake, citations_off_topic=off,
               citation_failure_rate=(fake + off) / cites if cites else None,
               variants_with_a_fake_citation=sum(1 for r in rows if r.get("llm_fake")),
               mean_confidence_when_wrong=_mean([r.get("llm_conf") for r in rows
                                                 if r.get("llm_pred") and r["llm_pred"] != r["truth"]]))
    ae.update(citations=sum(r.get("aegis_cites", 0) for r in rows),
              citations_rejected_by_verifier=sum(r.get("aegis_rejected", 0) for r in rows),
              citation_failure_rate=0.0 if sum(r.get("aegis_cites", 0) for r in rows) else None,
              wrong_calls=sum(1 for r in rows if r.get("aegis_pred") and r["aegis_pred"] != r["truth"]))
    llm["wrong_calls"] = sum(1 for r in rows if r.get("llm_pred") and r["llm_pred"] != r["truth"])
    paired = mcnemar([bool(r.get("llm_pred") == r["truth"]) for r in rows],
                     [bool(r.get("aegis_pred") == r["truth"]) for r in rows]) if rows else {}
    return {"mode": mode, "n": n, "llm_only": llm, "aegis": ae, "paired_correct": paired,
            "date": time.strftime("%Y-%m-%d")}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def _pct(x):
    return "–" if x is None else f"{x:.0%}"


def render(s: dict, rows: list[dict], mode: str) -> str:
    e = lambda x: html.escape("" if x is None else str(x))
    L, A = s["llm_only"], s["aegis"]
    banner = ("<p class='warn'>OFFLINE demo data: synthetic variants and a scripted stand-in for both arms. "
              "Run with your API key on the ClinVar benchmark for real numbers.</p>") if mode == "offline" else ""

    def bar(label, a, b, fmt=_pct, lower_better=False):
        mx = max([x for x in (a, b) if x is not None] + [1e-9])
        w = lambda x: 0 if x is None else max(x / mx * 260, 2)
        return (f"<tr><td>{label}{' <span class=muted>(lower is better)</span>' if lower_better else ''}</td>"
                f"<td><div class='b' style='width:{w(a):.0f}px;background:#eb6834'></div> {fmt(a)}</td>"
                f"<td><div class='b' style='width:{w(b):.0f}px;background:#2a78d6'></div> {fmt(b)}</td></tr>")

    head = "".join([
        bar("Correct on all variants", L["correct_over_all"], A["correct_over_all"]),
        bar("Accuracy when it answers", L["accuracy_answered"], A["accuracy_answered"]),
        bar("Cited papers that are fake or off-topic", L["citation_failure_rate"], A["citation_failure_rate"],
            lower_better=True),
        bar("Wrong calls", L["wrong_calls"], A["wrong_calls"], fmt=lambda x: e(x), lower_better=True),
        bar("Declined (said 'not enough evidence')", L["declined"], A["declined"], fmt=lambda x: e(x)),
        bar("Cost per variant (USD)", L["mean_cost_usd"], A["mean_cost_usd"], fmt=lambda x: f"${x:.3f}" if x is not None else "–",
            lower_better=True),
        bar("Median time per variant (s)", L["median_secs"], A["median_secs"], fmt=lambda x: f"{x:.1f}s" if x is not None else "–",
            lower_better=True),
    ])

    def cite_cell(r):
        out = []
        for c in r.get("llm_checks") or []:
            tag = "✓" if c["exists"] and c["mentions_gene"] else ("✗ does not exist" if not c["exists"] else "✗ off-topic")
            out.append(f"<a href='https://pubmed.ncbi.nlm.nih.gov/{e(c['pmid'])}/'>{e(c['pmid'])}</a> {tag}")
        return "<br>".join(out) or "<span class=muted>none</span>"

    body = "".join(
        f"<tr><td class=mono>{e(r['variant'])}</td><td><b>{e(r['truth'])}</b></td>"
        f"<td class='{'ok' if r.get('llm_pred') == r['truth'] else 'bad'}'>{e(r.get('llm_label'))} "
        f"<span class=muted>(conf {e(r.get('llm_conf'))})</span></td><td class=small>{cite_cell(r)}</td>"
        f"<td class='{'ok' if r.get('aegis_pred') == r['truth'] else ('muted' if r.get('aegis_declined') else 'bad')}'>"
        f"{e(r.get('aegis_label'))} <span class=muted>({e(r.get('aegis_points'))} pts)</span></td>"
        f"<td class='small mono'>{e(r.get('aegis_criteria'))}<br>{e(r.get('aegis_cites'))} verified, {e(r.get('aegis_rejected'))} rejected</td></tr>"
        for r in rows)
    p = s.get("paired_correct") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>LLM alone vs Aegis</title><style>
body{{font:14px/1.5 -apple-system,Segoe UI,Inter,sans-serif;max-width:1200px;margin:26px auto;padding:0 18px;color:#151a22}}
h1{{font-size:22px}}h2{{font-size:15px;border-bottom:1px solid #e3e6eb;padding-bottom:4px;margin-top:26px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:6px 8px;border-bottom:1px solid #eceef2;text-align:left;vertical-align:top}}
th{{font-size:11.5px;color:#5d6675;text-transform:uppercase}}.b{{display:inline-block;height:12px;border-radius:3px;vertical-align:middle}}
.muted{{color:#5d6675}}.mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px}}.small{{font-size:12px}}
.ok{{color:#1d6b43}}.bad{{color:#a32222}}.warn{{background:#fff7e6;border:1px solid #f0dcae;border-radius:8px;padding:8px 12px}}
.legend span{{display:inline-block;width:11px;height:11px;border-radius:2px;margin:0 5px 0 12px;vertical-align:-1px}}
</style></head><body>
<h1>A frontier LLM on its own vs Aegis</h1>
<p class=muted>{s['n']} variants · scored against ClinVar · {e(s['date'])} · ClinVar hidden from Aegis; the LLM may have memorised it</p>{banner}
<div class=legend><span style="background:#eb6834"></span>LLM on its own (strongest model, no tools)<span style="background:#2a78d6"></span>Aegis</div>
<table><tr><th>Measure</th><th>LLM on its own</th><th>Aegis</th></tr>{head}</table>
<p class=muted>The LLM cited {e(L['citations'])} papers: {e(L['citations_nonexistent'])} do not exist in PubMed and {e(L['citations_off_topic'])} exist but never mention the gene;
{e(L['variants_with_a_fake_citation'])} variant(s) had at least one invented citation; average confidence on its wrong calls: {e(L['mean_confidence_when_wrong'])}.
Aegis cited {e(A['citations'])} papers, each quote checked verbatim against the abstract; its verifier rejected {e(A['citations_rejected_by_verifier'])} claims before they reached a report.
Paired comparison of correct calls (exact McNemar): Aegis-only correct {e(p.get('only_b_ok'))}, LLM-only correct {e(p.get('only_a_ok'))}, p = {e(round(p['p_value'], 4) if p.get('p_value') is not None else None)}.</p>
<h2>Variant by variant</h2>
<table><tr><th>Variant</th><th>ClinVar</th><th>LLM call</th><th>LLM's citations (checked in PubMed)</th><th>Aegis call</th><th>Aegis evidence</th></tr>{body}</table>
</body></html>"""
