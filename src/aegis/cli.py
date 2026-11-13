"""Command line interface.  Run `aegis -h` for the full list."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .config import Settings


def _rt(args, **over):
    from .services import RunOptions, Runtime

    s = Settings()
    mode = getattr(args, "mode", None) or ("live" if s.has_llm() else "offline")
    opts = RunOptions(mode=mode, faults=getattr(args, "faults", "none") or "none",
                      allow_browser=not getattr(args, "no_browser", False), **over)
    return Runtime(s, opts)


def _print_run(rt, rid: str, full: bool = False) -> None:
    from .tracing.taxonomy import label_run

    r = rt.store.get_run(rid)
    out = (r.get("result") or {}).get("output") or {}
    usage = (r.get("result") or {}).get("usage") or {}
    print(f"\nrun {rid}  [{r['workload']}]  status={r['status']}" + (f"  error={r['error']}" if r["error"] else ""))
    if r["workload"] == "variant" and out:
        print(f"  {out.get('variant')}  gene={out.get('gene')}  ->  {out.get('classification')}  (points {out.get('points')})")
        for c in out.get("criteria") or []:
            print(f"    {c['code']:<18} {c['points']:+d}  {c['rationale']}")
        if out.get("report"):
            print("\n  " + out["report"].replace("\n", "\n  "))
        print(f"\n  citations: {out.get('citations')}  rejected evidence: {out.get('rejected_evidence')}")
        if out.get("browser_crosscheck"):
            print(f"  browser cross-check: {out['browser_crosscheck']}")
    elif r["workload"] == "company" and out:
        b = out.get("brief") or {}
        print(f"  {b.get('one_liner')}\n  {b.get('summary')}")
        for f in out.get("facts", []):
            print(f"   - {f['field']}: {f['value']}  [{f['source_url']}]")
        print(f"  browser: {out.get('browser')}")
        if out.get("outreach"):
            print(f"  outreach: {out['outreach'].get('subject')} -> {out['outreach'].get('send')}")
    if r["status"] == "waiting_approval":
        for a in rt.store.list_approvals("pending", rid):
            print(f"  APPROVAL {a['id']} pending: {a['tool']} {json.dumps(a['args'])[:300]}")
            print(f"  -> aegis approve {a['id']}    or    aegis approve {a['id']} --reject")
    if usage:
        print(f"\n  usage: {usage.get('llm_calls')} LLM calls, ${usage.get('cost_usd')}, {usage.get('tool_calls')} tool calls, "
              f"{usage.get('tool_retries')} retries, models={usage.get('models')}")
    labels = label_run(rt.store.spans(rid), rt.store.events(rid))
    if labels:
        print(f"  trace labels: {dict(labels)}")
    if full:
        print(json.dumps(r.get("result"), indent=2, default=str))


async def cmd_variant(a):
    rt = _rt(a)
    rid = await rt.run("variant", {"variant": a.variant, "phenotype": a.phenotype or "", "use_browser": not a.no_browser})
    _print_run(rt, rid, a.json)


async def cmd_company(a):
    rt = _rt(a)
    sender = {"name": a.sender_name, "company": a.sender_company, "offer": a.offer}
    rid = await rt.run("company", {"start_url": a.url, "domain": a.domain, "draft_outreach": a.outreach, "sender": sender})
    _print_run(rt, rid, a.json)


async def cmd_resume(a):
    rt = _rt(a)
    await rt.resume(a.run_id)
    _print_run(rt, a.run_id)


def cmd_cancel(a):
    _rt(a).engine.cancel(a.run_id)
    print("cancel requested")


def cmd_runs(a):
    rt = _rt(a)
    for r in rt.store.list_runs(a.limit):
        print(f"{r['run_id']}  {r['workload']:<8} {r['status']:<17} {(r['error'] or '')[:80]}")


def cmd_show(a):
    _print_run(_rt(a), a.run_id, a.json)


def cmd_approvals(a):
    for x in _rt(a).store.list_approvals("pending"):
        print(f"{x['id']}  run={x['run_id']}  {x['tool']}  risk={x['risk']}  {json.dumps(x['args'])[:200]}")


async def cmd_approve(a):
    rt = _rt(a)
    row = rt.store.decide_approval(a.approval_id, not a.reject, by=os.environ.get("USER", "human"))
    print(f"approval {a.approval_id}: {row['status']}; resuming run {row['run_id']}")
    await rt.resume(row["run_id"])
    _print_run(rt, row["run_id"])


async def cmd_eval(a):
    from .evals.harness import load_cases, offline_cases, run_suite, summarize, to_markdown
    from .evals.report import write_report

    mode = a.mode or "offline"
    if mode == "offline":
        os.environ.setdefault("AEGIS_BACKOFF_SCALE", "0.02")
        cases = offline_cases(a.n, a.seed)
        train = None
    else:
        cases = load_cases(a.cases, "test", a.n)
        train = load_cases(a.cases, "train", a.n) if a.calibrate else None
    systems = a.systems.split(",")
    profiles = a.profiles.split(",")
    print(f"eval: {len(cases)} cases x {systems} x {profiles}  mode={mode}")
    rows = await run_suite(cases, systems, profiles, mode=mode, seed=a.seed, out_dir=a.out, concurrency=a.concurrency)
    train_rows = None
    if train:
        train_rows = await run_suite(train, systems, profiles, mode=mode, seed=a.seed, out_dir=a.out + "/train",
                                     concurrency=a.concurrency)
    s = summarize(rows, train_rows)
    title = f"Aegis eval ({mode}, {len(cases)} cases)"
    md = to_markdown(s, title, mode)
    write_report(s, rows, a.out, mode, title, md)
    print(md)
    print(f"report: {a.out}/report.html")
    if a.gate:
        g = {(x["system"], x["profile"]): x for x in s["groups"]}
        bad = [k for k, v in g.items() if k[0] == "aegis" and (v["success_rate"] < a.gate)]
        if bad:
            print(f"QUALITY GATE FAILED: success_rate < {a.gate} for {bad}")
            sys.exit(1)
        print("quality gate passed")


async def cmd_batch(a):
    from .batch import finish_batch, parse_input, register_batch, run_batch

    rt = _rt(a)
    parsed = parse_input(a.file, a.include_filtered)
    name = a.name or os.path.splitext(os.path.basename(a.file))[0]
    out = a.out or f"data/batches/{name}"
    print(f"batch '{name}': {len(parsed.variants)} variants, {len(parsed.skipped)} skipped")
    for raw, why in parsed.skipped[:20]:
        print(f"  skipped {raw}: {why}")
    if a.limit:
        parsed.variants = parsed.variants[: a.limit]
    register_batch(rt.store, name, a.file, parsed, out)
    summary = await run_batch(rt, parsed.variants, name, out, a.concurrency, a.phenotype or "", a.browser)
    finish_batch(rt.store, name, summary)
    print(json.dumps(summary, indent=2))
    print(f"open {summary['report']}")


def cmd_report(a):
    from .reports.variant_report import render

    rt = _rt(a)
    out = a.out or f"data/reports/{a.run_id}.html"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        f.write(render(rt.store, a.run_id))
    print(f"wrote {out}")


async def cmd_watch(a):
    from .watch import Watchlist, write_digest

    rt = _rt(a)
    w = Watchlist(rt.store)
    if a.action == "add":
        for v in a.variants:
            w.add(v, a.note or "")
        print(f"watching {len(w.list())} variants")
    elif a.action == "remove":
        for v in a.variants:
            w.remove(v)
    elif a.action == "list":
        for r in w.list():
            last = r["last"] or {}
            print(f"{r['variant']:<24} {last.get('label') or '(not checked yet)':<22} {r['note'] or ''}")
    elif a.action == "changes":
        for c in w.changes():
            print(f"{c['variant']}  {c['changes']}")
    elif a.action == "run":
        report = await w.run(rt)
        path = write_digest(report)
        print(f"checked {report['checked']}, changed {len(report['changed'])}, failed {len(report['failed'])}; digest: {path}")


async def cmd_compare(a):
    from .evals.compare import run_compare
    from .evals.harness import Case, load_cases, offline_cases

    mode = a.mode or ("live" if Settings().has_llm() else "offline")
    if a.variants:
        truths = (a.truth or "").split(",") if a.truth else [None] * len(a.variants)
        cases = [Case(f"v{i}", v, t or None) for i, (v, t) in enumerate(zip(a.variants, truths))]
    elif mode == "offline":
        cases = offline_cases(a.n, 0)
    else:
        cases = load_cases(a.cases, "test", a.n)
    print(f"comparing LLM-alone vs Aegis on {len(cases)} variants (mode={mode})")
    s = await run_compare(cases, Settings(), mode, a.out, a.concurrency)
    print(json.dumps({k: s[k] for k in ("n", "llm_only", "aegis", "paired_correct")}, indent=2, default=str))
    print(f"open {a.out}/compare.html")


def cmd_bench_build(a):
    from .evals.build_clinvar import build, download

    src = a.source or download()
    print(json.dumps(build(src, a.out, a.cutoff, a.n_per_class, a.n_per_class), indent=2, default=str))


async def cmd_judge(a):
    from .evals.judge_calibration import calibrate

    rt = _rt(a)
    svc = rt.make_services(rt.store.create_run("variant", {"variant": "calibration"}), {})
    print(json.dumps(await calibrate(svc.router, a.labels), indent=2))


def cmd_export(a):
    from .tracing.export import export_failures

    print(f"exported {export_failures(_rt(a).store, a.out)} cases to {a.out}")


def cmd_memory(a):
    m = _rt(a).memory()
    if a.action == "list":
        for l in m.list():
            print(f"{l['id']}  {l['status']:<9} {l['text']}")
    else:
        print(m.set_status(a.lesson_id, {"promote": "active", "retire": "retired"}[a.action]))


def cmd_serve(a):
    import uvicorn

    if a.mode:
        os.environ["AEGIS_MODE"] = a.mode
    uvicorn.run("aegis.api.server:app", host=a.host, port=a.port)


def cmd_mcp(a):
    from .mcp_server import main

    main()


async def cmd_doctor(a):
    """Check keys (masked), browser, and reachability of every upstream."""
    import shutil

    from .tools.http import Http

    s = Settings()
    mask = lambda k: (k[:7] + "…" + k[-4:]) if k else "not set"
    print("ANTHROPIC_API_KEY:", mask(s.anthropic_key))
    print("OPENAI_API_KEY:   ", mask(s.openai_key), " models:", s.openai_fast, s.openai_balanced, s.openai_strong)
    print("LOCAL LLM:        ", s.local_base_url, s.local_model)
    print("NCBI_API_KEY:     ", mask(s.ncbi_key), " NCBI_EMAIL:", s.ncbi_email or "not set (recommended)")
    print("tiers:", {k: [str(r) for r in v] for k, v in s.tiers().items()})
    chrome = s.chrome_path or shutil.which("google-chrome") or shutil.which("chromium")
    print("browser:", chrome or "Playwright-managed Chromium (run: python -m playwright install chromium)")
    http = Http()
    checks = {
        "ensembl": ("GET", "https://rest.ensembl.org/info/ping?content-type=application/json", {}),
        "ncbi": ("GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json", {}),
        "gnomad": ("POST", "https://gnomad.broadinstitute.org/api", {"json": {"query": "{ __typename }"}}),
    }
    for name, (m, url, kw) in checks.items():
        try:
            r = await http.request(m, url, **kw)
            print(f"  {name:<8} OK {r.status_code}")
        except Exception as e:
            print(f"  {name:<8} FAIL {type(e).__name__}: {e}")
    await http.close()
    if s.has_llm():
        from .services import RunOptions, Runtime
        from .llm.base import LLMRequest

        rt = Runtime(s, RunOptions(mode="live"))
        svc = rt.make_services(rt.store.create_run("variant", {"variant": "doctor"}), {})
        for tier in ("fast", "balanced", "strong"):
            try:
                r = await svc.router.complete(LLMRequest(system="Reply with OK.", messages=[{"role": "user", "content": "ping"}],
                                                         tier=tier, purpose="doctor", max_tokens=5))
                print(f"  llm {tier:<8} OK {r.provider}:{r.model} {r.latency_ms:.0f}ms ${r.cost_usd:.6f}")
            except Exception as e:
                print(f"  llm {tier:<8} FAIL {type(e).__name__}: {str(e)[:160]}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="aegis", description="Aegis: reliability runtime for AI agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        sp.add_argument("--mode", choices=["live", "record", "replay", "offline"], default=None)
        return sp

    sp = add("variant", cmd_variant, "curate evidence for a GRCh38 variant, e.g. 17-43045712-T-C")
    sp.add_argument("variant"); sp.add_argument("--phenotype"); sp.add_argument("--faults", default="none")
    sp.add_argument("--no-browser", action="store_true"); sp.add_argument("--json", action="store_true")
    sp = add("company", cmd_company, "research a company website into a verified brief")
    sp.add_argument("url"); sp.add_argument("--domain"); sp.add_argument("--outreach", action="store_true")
    sp.add_argument("--sender-name", default="Umesh"); sp.add_argument("--sender-company", default="")
    sp.add_argument("--offer", default=""); sp.add_argument("--faults", default="none")
    sp.add_argument("--json", action="store_true")
    sp = add("resume", cmd_resume, "resume a crashed/failed/approved run"); sp.add_argument("run_id")
    sp = add("cancel", cmd_cancel, "request cancellation"); sp.add_argument("run_id")
    sp = add("runs", cmd_runs, "list runs"); sp.add_argument("--limit", type=int, default=30)
    sp = add("show", cmd_show, "show a run"); sp.add_argument("run_id"); sp.add_argument("--json", action="store_true")
    add("approvals", cmd_approvals, "list pending approvals")
    sp = add("approve", cmd_approve, "approve (or --reject) and resume"); sp.add_argument("approval_id", type=int)
    sp.add_argument("--reject", action="store_true")
    sp = add("eval", cmd_eval, "run the evaluation suite")
    sp.add_argument("--systems", default="baseline,aegis"); sp.add_argument("--profiles", default="none,harsh,crash_mid")
    sp.add_argument("--n", type=int, default=60); sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--cases", default="evals/data/clinvar_benchmark.jsonl"); sp.add_argument("--calibrate", action="store_true")
    sp.add_argument("--out", default="evals/results/latest"); sp.add_argument("--concurrency", type=int, default=4)
    sp.add_argument("--gate", type=float, default=None, help="fail (exit 1) if aegis success_rate is below this")
    sp = add("batch", cmd_batch, "curate every variant in a VCF / CSV / TSV (resumable)")
    sp.add_argument("file"); sp.add_argument("--name"); sp.add_argument("--out"); sp.add_argument("--phenotype")
    sp.add_argument("--concurrency", type=int, default=4); sp.add_argument("--limit", type=int)
    sp.add_argument("--include-filtered", action="store_true"); sp.add_argument("--browser", action="store_true",
                                                                                  help="also run the ClinVar browser cross-check")
    sp.add_argument("--faults", default="none")
    sp = add("report", cmd_report, "write the HTML report for a variant run"); sp.add_argument("run_id")
    sp.add_argument("--out")
    sp = add("watch", cmd_watch, "watchlist: add / remove / list / run / changes")
    sp.add_argument("action", choices=["add", "remove", "list", "run", "changes"]); sp.add_argument("variants", nargs="*")
    sp.add_argument("--note")
    sp = add("compare", cmd_compare, "head-to-head: frontier LLM alone vs Aegis, scored against ClinVar")
    sp.add_argument("--cases", default="evals/data/clinvar_benchmark.jsonl"); sp.add_argument("--n", type=int, default=30)
    sp.add_argument("--variants", nargs="*"); sp.add_argument("--truth", help="comma list of P/LP,B/LB,VUS for --variants")
    sp.add_argument("--out", default="evals/results/compare"); sp.add_argument("--concurrency", type=int, default=3)
    sp = add("bench-build", cmd_bench_build, "build the ClinVar temporal-split benchmark")
    sp.add_argument("--source"); sp.add_argument("--out", default="evals/data/clinvar_benchmark.jsonl")
    sp.add_argument("--cutoff", default="2024-06-30"); sp.add_argument("--n-per-class", type=int, default=40)
    sp = add("judge-calibrate", cmd_judge, "compare LLM judges with human labels (Cohen's kappa)")
    sp.add_argument("--labels", default="evals/data/judge_labels.jsonl")
    sp = add("export-failures", cmd_export, "turn failed traces into regression cases")
    sp.add_argument("--out", default="evals/data/regressions.jsonl")
    sp = add("memory", cmd_memory, "list / promote / retire learned lessons")
    sp.add_argument("action", choices=["list", "promote", "retire"]); sp.add_argument("lesson_id", nargs="?")
    sp = add("serve", cmd_serve, "API + live trace explorer"); sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8080)
    add("mcp", cmd_mcp, "run as an MCP server (stdio)")
    add("doctor", cmd_doctor, "check configuration and connectivity")

    a = p.parse_args(argv)
    res = a.fn(a)
    if asyncio.iscoroutine(res):
        asyncio.run(res)


if __name__ == "__main__":
    main()
