"""Product features: VCF/CSV parsing, resumable batches, reports, watchlist diffs, LLM-vs-Aegis comparison."""
import json
from pathlib import Path

from aegis.batch import parse_input, run_batch, run_id_for
from aegis.reports.variant_report import render
from aegis.watch import Watchlist
from aegis.workloads.variant.fixtures import fixtures

EX = Path(__file__).parent.parent / "examples" / "demo_offline.vcf"


def test_vcf_parsing_rules(tmp_path):
    p = parse_input(str(EX))
    reasons = {why for _, why in p.skipped}
    assert len(p.variants) == 11
    assert {"symbolic/structural allele", "FILTER=LowQual", "mitochondrial (not supported)"} <= reasons
    assert all(not v.startswith("chr") for v in p.variants)
    csvp = tmp_path / "v.csv"
    csvp.write_text("chrom,pos,ref,alt\nchr17,43045712,T,C\n17,43045712,T,C\nX,100,A,AT\nbad,row,,\n")
    t = parse_input(str(csvp))
    assert t.variants == ["17-43045712-T-C", "X-100-A-AT"] and len(t.skipped) == 1


async def test_batch_resumes_and_writes_outputs(offline_runtime, tmp_path):
    rt = offline_runtime()
    variants = parse_input(str(EX)).variants[:5]
    out = tmp_path / "b"
    s1 = await run_batch(rt, variants, "t1", str(out), concurrency=3, progress=lambda *_: None)
    assert s1["progress"] == {"new": 5}
    s2 = await run_batch(rt, variants, "t1", str(out), concurrency=3, progress=lambda *_: None)
    assert s2["progress"] == {"already_done": 5}
    rows = (out / "results.tsv").read_text().splitlines()
    assert len(rows) == 6 and rows[0].startswith("variant\tgene")
    assert (out / "index.html").exists() and len(list((out / "reports").glob("*.html"))) == 5


async def test_report_contains_evidence_and_provenance(offline_runtime):
    rt = offline_runtime()
    vid = next(v for v, d in fixtures(0).items() if d["truth"] == "P/LP" and d["papers"])
    rid = await rt.run("variant", {"variant": vid})
    page = render(rt.store, rid)
    for needle in ("Criteria applied", "Provenance", "pubmed.ncbi.nlm.nih.gov", "research use only", vid):
        assert needle in page


def test_watch_diff():
    old = {"status": "succeeded", "label": "VUS", "points": 3, "criteria": ["PM2_Supporting", "PP3_Moderate"],
           "citations": [], "clinvar": "Uncertain significance", "papers_seen": ["1"]}
    new = {**old, "label": "Likely pathogenic", "points": 7, "criteria": ["PM2_Supporting", "PP3_Strong", "PS3_Supporting"],
           "citations": ["2"], "papers_seen": ["1", "2"]}
    fields = {c["field"] for c in Watchlist.diff(old, new)}
    assert fields == {"label", "points", "criteria", "citations", "new_papers"}
    assert Watchlist.diff(None, new) == []
    assert Watchlist.diff(old, {**new, "status": "failed"})[0]["field"] == "status"


async def test_watchlist_run_records_baseline_then_no_change(offline_runtime):
    rt = offline_runtime()
    w = Watchlist(rt.store)
    v = list(fixtures(0))[0]
    w.add(v, "fam 1")
    r1 = await w.run(rt, progress=lambda *_: None)
    assert r1["checked"] == 1 and r1["changed"] == []
    assert w.list()[0]["last"]["label"]


async def test_compare_offline(tmp_path):
    from aegis.config import Settings
    from aegis.evals.compare import run_compare
    from aegis.evals.harness import offline_cases

    s = await run_compare(offline_cases(8, 0), Settings(), "offline", str(tmp_path), concurrency=4)
    assert s["llm_only"]["citations_nonexistent"] > 0       # the problem being measured
    assert s["aegis"]["citation_failure_rate"] in (0.0, None)
    assert (tmp_path / "compare.html").exists()


async def test_outreach_compare_offline(site, tmp_path, monkeypatch):
    """Plain-LLM opener invents details; Aegis opener only keeps cited, verified sentences."""
    from aegis.config import Settings
    from aegis.evals.outreach_compare import run_outreach_compare

    from .conftest import CHROME

    if CHROME is None:
        import pytest

        pytest.skip("no Chrome")
    s = Settings()
    s.chrome_path = CHROME
    summary = await run_outreach_compare([f"{site}/index.html"], s, "offline", str(tmp_path), concurrency=1)
    assert summary["naive"]["unsupported_claims"] >= 1
    assert summary["aegis"]["unsupported_claims"] == 0
    row = json.loads((tmp_path / "rows.jsonl").read_text().splitlines()[0])
    assert row["aegis_dropped"] >= 1  # the uncited "grown fast" sentence was removed by code
    assert (tmp_path / "outreach_compare.html").exists()
