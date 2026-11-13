"""End-to-end runs: offline variant workload, eval harness, and real-Chrome browser runs on a flaky local site."""
import asyncio
import json

import pytest

from aegis.core.errors import DomainNotAllowed, PermissionDenied
from aegis.core.errors import SimulatedCrash
from aegis.workloads.variant.fixtures import fixtures

from .conftest import CHROME, needs_chrome


async def test_variant_offline_run_is_sourced_and_grounded(offline_runtime):
    rt = offline_runtime()
    vid = next(v for v, d in fixtures(0).items() if d["truth"] == "P/LP" and d["papers"])
    rid = await rt.run("variant", {"variant": vid})
    run = rt.store.get_run(rid)
    assert run["status"] == "succeeded"
    out = run["result"]["output"]
    assert out["classification"] and out["criteria"] is not None
    assert all(c.startswith("PMID:9") for c in out["citations"])
    assert "[removed]" not in (out["report"] or "")


async def test_hallucinated_citations_rejected_by_verifier(offline_runtime, monkeypatch):
    monkeypatch.setenv("AEGIS_OFFLINE_HALLUCINATION", "1.0")  # every triage emits a bad citation
    rt = offline_runtime()
    rejected = 0
    for vid, d in list(fixtures(0).items())[:20]:
        if not any("abolish" in p["abstract"] or "comparable" in p["abstract"] or "retained" in p["abstract"]
                   or "reduced" in p["abstract"] or "lost" in p["abstract"] for p in d["papers"]):
            continue
        rid = await rt.run("variant", {"variant": vid})
        rejected += len(rt.store.events(rid, "citation_rejected"))
        assert "PMID:99999999" not in rt.store.get_run(rid)["result"]["output"]["citations"]
    assert rejected > 0


async def test_ungrounded_report_numbers_repaired(offline_runtime, monkeypatch):
    monkeypatch.setenv("AEGIS_OFFLINE_HALLUCINATION", "1.0")
    rt = offline_runtime()
    rid = await rt.run("variant", {"variant": list(fixtures(0))[1]})
    rep = rt.store.get_steps(rid)["report"]["output"]
    assert rep["repaired"] is True and "17 unrelated families" not in rep["summary"]


async def test_harsh_faults_recovered_and_crash_resumed(offline_runtime):
    rt = offline_runtime(faults="harsh", seed=3)
    for vid in list(fixtures(3))[:8]:
        rid = await rt.run("variant", {"variant": vid})
        assert rt.store.get_run(rid)["status"] == "succeeded", rt.store.get_run(rid)["error"]
    rt2 = offline_runtime(faults="crash_mid")
    vid = list(fixtures(0))[0]
    with pytest.raises(SimulatedCrash):
        await rt2.run("variant", {"variant": vid}, run_id="crashme")
    rt3 = offline_runtime(faults="crash_mid")
    rt3.store = rt2.store
    rt3.engine.store = rt2.store
    from aegis.core.state import RunStatus

    assert await rt3.resume("crashme") == RunStatus.SUCCEEDED
    usage = rt2.store.get_run("crashme")["result"]["usage"]
    assert usage["llm_calls"] >= 6  # totals survive the "process" restart


async def test_eval_harness_small(tmp_path):
    from aegis.evals.harness import offline_cases, run_suite, summarize

    rows = await run_suite(offline_cases(12, 0), ["baseline", "aegis"], ["harsh"], mode="offline",
                           out_dir=str(tmp_path), concurrency=6)
    s = summarize(rows)
    g = {(x["system"], x["profile"]): x for x in s["groups"]}
    assert g[("aegis", "harsh")]["success_rate"] > g[("baseline", "harsh")]["success_rate"]
    assert s["paired"][0]["only_b_ok"] >= s["paired"][0]["only_a_ok"]


# ----------------------------------------------------------------- browser
@needs_chrome
async def test_browser_ladder_recovers_late_js_and_overlay(site):
    from aegis.tools.browser import BrowserAction, BrowserSession

    async with BrowserSession(["127.0.0.1"], CHROME) as b:
        obs = await b.goto(f"{site}/index.html")
        prod = next(e for e in obs.elements if e.text == "Products")
        out = await b.act(BrowserAction(kind="click", target_id=prod.id), obs)
        assert out.obs.url.endswith("/products.html") and out.strategy == "wait_settle"
        obs = await b.goto(f"{site}/index.html")
        pricing = next(e for e in obs.elements if e.text == "Pricing")
        out = await b.act(BrowserAction(kind="click", target_id=pricing.id), obs)
        assert out.obs.url.endswith("/pricing.html") and out.strategy != "direct"


@needs_chrome
async def test_browser_without_ladder_fails_on_flaky_ui(site):
    from aegis.core.errors import BrowserNoEffect
    from aegis.tools.browser import BrowserAction, BrowserSession

    async with BrowserSession(["127.0.0.1"], CHROME, ladder=False) as b:
        obs = await b.goto(f"{site}/index.html")
        pricing = next(e for e in obs.elements if e.text == "Pricing")
        with pytest.raises(BrowserNoEffect):
            await b.act(BrowserAction(kind="click", target_id=pricing.id), obs)


@needs_chrome
async def test_browser_policy_blocks_offsite_and_risky(site):
    from aegis.tools.browser import BrowserAction, BrowserSession

    async with BrowserSession(["127.0.0.1"], CHROME) as b:
        with pytest.raises(DomainNotAllowed):
            await b.goto("https://evil.example.net/steal")
        obs = await b.goto(f"{site}/index.html")
        sub = next(e for e in obs.elements if e.text == "Subscribe")
        with pytest.raises(PermissionDenied):
            await b.act(BrowserAction(kind="click", target_id=sub.id), obs)


@needs_chrome
async def test_company_run_injection_flagged_approval_and_exactly_once(offline_runtime, site):
    rt = offline_runtime()
    rid = await rt.run("company", {"start_url": f"{site}/index.html", "domain": "127.0.0.1", "draft_outreach": True,
                                   "sender": {"name": "Umesh"}})
    st = rt.store
    assert st.get_run(rid)["status"] == "waiting_approval"
    assert st.events(rid, "injection_detected")
    out = st.get_steps(rid)["browse"]["output"]
    assert out["browser_stats"]["recovered"] >= 1
    assert all("ignore previous instructions" not in p["text"] for p in out["pages"])
    appr = st.list_approvals("pending", rid)[0]
    st.decide_approval(appr["id"], True)
    await rt.resume(rid)
    assert st.get_run(rid)["status"] == "succeeded"
    facts = st.get_run(rid)["result"]["output"]["facts"]
    assert facts and all(f["verified"] for f in facts)
    assert len(st._all("SELECT * FROM effects")) == 1
