"""Self-contained, printable HTML report for one variant run.

Everything a reviewer needs to check the call without trusting the tool:
the classification and how many points each criterion contributed, the raw
evidence behind each criterion, every literature quote with a PubMed link,
what was rejected and why, the ClinVar comparison, and full provenance
(sources, models, cost, run id, timestamps). Open in any browser; print to PDF.
"""
from __future__ import annotations

import html
import json
import time
from typing import Any

LABEL_COLORS = {
    "Pathogenic": ("#fde8e8", "#a32222"), "Likely pathogenic": ("#fdeee6", "#a34a22"),
    "VUS": ("#f1f1ef", "#52514e"), "Likely benign": ("#e6f3ec", "#1d6b43"), "Benign": ("#e1f1e8", "#145536"),
    "insufficient_evidence": ("#f3eefb", "#5a3d9a"),
}

CSS = """
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;color:#151a22;max-width:900px;margin:28px auto;padding:0 20px;background:#fff}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:26px 0 8px;padding-bottom:4px;border-bottom:1px solid #e3e6eb}
.sub{color:#5d6675}.badge{display:inline-block;padding:4px 12px;border-radius:14px;font-weight:650;font-size:15px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}.kv{border:1px solid #e3e6eb;border-radius:8px;padding:8px 10px}
.kv .k{font-size:11.5px;color:#5d6675}.kv .v{font-weight:600;font-variant-numeric:tabular-nums;word-break:break-word}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eceef2;vertical-align:top}
th{font-size:11.5px;color:#5d6675;text-transform:uppercase;letter-spacing:.4px}
.pts{font-variant-numeric:tabular-nums;font-weight:650}.pos{color:#a32222}.neg{color:#1d6b43}
blockquote{margin:6px 0;padding:6px 10px;border-left:3px solid #2b59c3;background:#f6f8fc}
.muted{color:#5d6675}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.note{background:#fff7e6;border:1px solid #f0dcae;border-radius:8px;padding:8px 12px;font-size:13px}
@media print{body{margin:0}h2{break-after:avoid}}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.{nd}g}"
    return str(x)


def render(store, run_id: str) -> str:
    run = store.get_run(run_id)
    steps = {k: (v.get("output") or {}) for k, v in store.get_steps(run_id).items()}
    res = run.get("result") or {}
    out = res.get("output") or {}
    usage = res.get("usage") or {}
    con, pop, cons = steps.get("consequence", {}), steps.get("population", {}), steps.get("constraint", {})
    cls, ver, lit = steps.get("classify", {}), steps.get("verify", {}), steps.get("literature", {})
    cv, bx = steps.get("clinical", {}), steps.get("browser_xcheck", {})
    label = cls.get("label") or out.get("classification") or run["status"]
    bg, fg = LABEL_COLORS.get(label, ("#eef0f3", "#151a22"))
    papers = {p["pmid"]: p for p in lit.get("papers", [])}
    variant = run["input"].get("variant", "")

    crit_rows = "".join(
        f"<tr><td class='mono'>{_e(c['code'])}</td><td class='pts {'pos' if c['points'] > 0 else 'neg'}'>{c['points']:+d}</td>"
        f"<td>{_e(c['rationale'])}</td><td class='mono'>{_e(', '.join(c.get('sources', [])))}</td></tr>"
        for c in cls.get("criteria") or [])
    if not crit_rows:
        crit_rows = "<tr><td colspan=4 class='muted'>No criteria applied.</td></tr>"

    def paper_link(pmid: str) -> str:
        return f"<a href='https://pubmed.ncbi.nlm.nih.gov/{_e(pmid)}/'>PMID {_e(pmid)}</a>"

    accepted = "".join(
        f"<div><b>{_e(f['direction'])}</b> {_e(f['evidence_type'])} · {paper_link(f['pmid'])}"
        f" <span class='muted'>{_e((papers.get(f['pmid']) or {}).get('title', ''))[:140]}</span>"
        f"<blockquote>{_e(f['quote'])}</blockquote></div>" for f in ver.get("functional", []))
    rejected = "".join(
        f"<tr><td>{paper_link(r['pmid'])}</td><td>{_e(r.get('why'))}</td><td class='muted'>{_e(r.get('quote', ''))[:200]}</td></tr>"
        for r in ver.get("rejected", []))
    considered = "".join(
        f"<li>{paper_link(p['pmid'])} ({_e(p.get('year'))}) {_e(p.get('title'))[:160]}</li>" for p in lit.get("papers", []))

    clinvar_html = "<span class='muted'>not fetched</span>"
    if cv.get("masked"):
        clinvar_html = "<span class='muted'>hidden (evaluation mode)</span>"
    elif cv.get("found"):
        clinvar_html = (f"<a href='{_e(cv.get('url'))}'>{_e(cv.get('classification'))}</a> · {_e(cv.get('review_status'))}"
                        f" · last evaluated {_e(cv.get('last_evaluated'))}")
        if "clinvar_concordant" in cls:
            clinvar_html += " · <b>agrees</b>" if cls["clinvar_concordant"] else " · <b>differs from Aegis</b>"
    elif cv:
        clinvar_html = "<span class='muted'>no matching ClinVar record</span>"
    browser_html = ""
    if bx and not bx.get("skipped"):
        browser_html = (f"<div class='kv'><div class='k'>ClinVar web page (read in a browser)</div><div class='v'>"
                        f"{_e(bx.get('page_classification'))} · {'matches API' if bx.get('agrees') else 'DIFFERS from API' if bx.get('agrees') is False else 'n/a'}</div></div>")

    created = time.strftime("%Y-%m-%d %H:%M", time.localtime(run["created_at"]))
    models = ", ".join(usage.get("models") or []) or "–"
    narrative = (steps.get("report") or {}).get("summary") or out.get("report") or ""
    abstain = (f"<p class='note'>Aegis declined to classify: {_e(cls.get('abstain_reason'))}</p>"
               if cls.get("abstained") else "")
    degraded = res.get("degraded_steps") or []
    deg_html = (f"<p class='note'>Some sources were unavailable and the call was made without them: "
                f"{_e(', '.join(degraded))}.</p>") if degraded else ""

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_e(variant)} · Aegis report</title>
<style>{CSS}</style></head><body>
<div class="sub">Aegis variant evidence report · research use only, not for clinical decisions</div>
<h1>{_e(con.get('gene_symbol') or '')} {_e(con.get('hgvsc', '') or '').split(':')[-1]} {_e((con.get('hgvsp') or '').split(':')[-1])}</h1>
<div class="sub mono">{_e(variant)} (GRCh38) · {_e(con.get('transcript_id'))}{' · MANE ' + _e(con.get('mane_select')) if con.get('mane_select') else ''}</div>
<p><span class="badge" style="background:{bg};color:{fg}">{_e(label)}</span>
 <span class="pts" style="margin-left:8px">{_fmt(cls.get('points'))} points</span>
 <span class="muted">(ClinGen points: ≥10 P, 6–9 LP, 0–5 VUS, −1 to −6 LB, ≤−7 B)</span></p>
{abstain}{deg_html}
<div class="grid">
 <div class="kv"><div class="k">Consequence</div><div class="v">{_e(con.get('most_severe_consequence'))}</div></div>
 <div class="kv"><div class="k">gnomAD v4 FAF95 popmax</div><div class="v">{_fmt(pop.get('faf95_popmax'))} {('(' + _e(pop.get('faf95_population')) + ')') if pop.get('faf95_population') else ''}</div></div>
 <div class="kv"><div class="k">gnomAD AC / AN</div><div class="v">{_fmt(pop.get('ac'))} / {_fmt(pop.get('an'))}{'' if pop.get('found', True) else ' (absent)'}</div></div>
 <div class="kv"><div class="k">REVEL · AlphaMissense</div><div class="v">{_fmt(con.get('revel'), 3)} · {_fmt(con.get('alphamissense'), 3)}</div></div>
 <div class="kv"><div class="k">Exon</div><div class="v">{_e(con.get('exon'))}{' (last)' if con.get('is_last_exon') else ''}</div></div>
 <div class="kv"><div class="k">Gene LOEUF</div><div class="v">{_fmt(cons.get('loeuf'), 3)}</div></div>
 <div class="kv" style="grid-column:span 2"><div class="k">ClinVar (comparison only, never used as evidence)</div><div class="v">{clinvar_html}</div></div>
 {browser_html}
</div>
<h2>Criteria applied</h2>
<table><tr><th>Criterion</th><th>Points</th><th>Why</th><th>Source</th></tr>{crit_rows}</table>
<h2>Summary</h2><p>{_e(narrative)}</p>
<h2>Literature evidence that passed verification ({len(ver.get('functional', []))})</h2>
{accepted or "<p class='muted'>None.</p>"}
<p class="muted">Each quote was checked to appear verbatim in the cited abstract{', and confirmed by two independent model judges' if any(f.get('judges') for f in ver.get('functional', [])) else ''}. Literature evidence counts at Supporting strength only.</p>
<h2>Rejected evidence ({len(ver.get('rejected', []))})</h2>
{('<table><tr><th>Paper</th><th>Why rejected</th><th>Claimed quote</th></tr>' + rejected + '</table>') if rejected else "<p class='muted'>None.</p>"}
<h2>Papers considered ({len(lit.get('papers', []))})</h2>
<ul>{considered or "<li class='muted'>none found</li>"}</ul>
<p class="muted mono">PubMed query: {_e(lit.get('query'))} · retrieval: {_e((lit.get('retrieval') or {}).get('embedder'))}</p>
<h2>Provenance</h2>
<table>
<tr><td>Run</td><td class="mono">{_e(run_id)} · {_e(run['status'])} · created {created}</td></tr>
<tr><td>Sources</td><td>Ensembl VEP ({_e(con.get('source'))}), gnomAD ({_e(pop.get('dataset'))}), NCBI ClinVar, NCBI PubMed</td></tr>
<tr><td>Models</td><td class="mono">{_e(models)}</td></tr>
<tr><td>Usage</td><td>{_fmt(usage.get('llm_calls'))} model calls · ${_fmt(usage.get('cost_usd'), 3)} · {_fmt(usage.get('tool_calls'))} data calls · {_fmt(usage.get('tool_retries'))} retries</td></tr>
</table>
<p class="muted" style="margin-top:20px">ACMG/AMP criteria implemented: BA1, BS1, PM2_Supporting, PVS1 (simplified), PP3/BP4 (REVEL, ClinGen-calibrated thresholds), PS3/BS3 (capped at Supporting). Other criteria are not evaluated and could change this classification.</p>
</body></html>"""


def batch_page(title: str, rows: list[dict], counts: dict[str, int]) -> str:
    order = ["Pathogenic", "Likely pathogenic", "VUS", "Likely benign", "Benign", "insufficient_evidence", "failed"]
    total = max(sum(counts.values()), 1)
    bars = "".join(
        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'><div style='width:150px'>{_e(k)}</div>"
        f"<div style='height:14px;width:{max(counts.get(k, 0) / total * 420, 2):.0f}px;border-radius:3px;"
        f"background:{LABEL_COLORS.get(k, ('#eee', '#c0392b'))[1]}'></div><div class='pts'>{counts.get(k, 0)}</div></div>"
        for k in order if counts.get(k))
    body = "".join(
        f"<tr><td class='mono'>{_e(r['variant'])}</td><td>{_e(r.get('gene'))}</td><td>{_e(r.get('hgvsp_short') or r.get('consequence'))}</td>"
        f"<td><span class='badge' style='font-size:12px;padding:1px 8px;background:{LABEL_COLORS.get(r.get('classification'), ('#eee', '#333'))[0]};"
        f"color:{LABEL_COLORS.get(r.get('classification'), ('#eee', '#333'))[1]}'>{_e(r.get('classification') or r.get('status'))}</span></td>"
        f"<td class='pts'>{_e(r.get('points'))}</td><td class='mono'>{_e(r.get('criteria'))}</td>"
        f"<td>{_e(r.get('clinvar'))}</td><td>{('<a href=' + _e(r['report']) + '>report</a>') if r.get('report') else ''}</td></tr>"
        for r in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_e(title)}</title><style>{CSS}
body{{max-width:1200px}}</style></head><body><div class="sub">Aegis batch report · research use only</div><h1>{_e(title)}</h1>
<h2>Classifications</h2>{bars}
<h2>Variants ({len(rows)})</h2>
<table><tr><th>Variant</th><th>Gene</th><th>Change</th><th>Call</th><th>Points</th><th>Criteria</th><th>ClinVar</th><th></th></tr>{body}</table>
</body></html>"""
