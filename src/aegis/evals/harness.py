"""Evaluation harness: systems x fault profiles x cases, paired and reproducible.

Systems are the same code with features switched on or off, so a difference
in a metric is attributable to the feature, not to a different prompt or
model. Every run is a real run through the engine and leaves a full trace.

    baseline     sequential single agent: no retries, no checkpoint-based
                 degradation, no verifier, no judges, no grounding check,
                 single sample, no abstention
    aegis        everything on
    ablations    aegis minus one feature (no_verifier, no_retries, no_degrade,
                 no_self_consistency, no_judges)

Ground truth is ClinVar, so ClinVar is masked from the system under test
(eval_mask_clinvar) and the browser cross-check (which reads ClinVar) is off.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..core.errors import SimulatedCrash
from ..core.state import RunStatus
from ..core.store import Store
from ..services import RunOptions, Runtime
from ..tracing.taxonomy import label_run
from ..workloads.variant import acmg
from ..workloads.variant.workload import ungrounded_numbers
from . import metrics as M

ALL_ON = {"retries": True, "step_retry": True, "degrade": True, "verifier": True, "judge_panel": True,
          "grounding": True, "self_consistency": True, "abstain": True, "memory": False, "browser_ladder": True}
ALL_OFF = {k: False for k in ALL_ON}

SYSTEMS: dict[str, tuple[dict, int]] = {
    "baseline": (ALL_OFF, 1),
    "aegis": (ALL_ON, 6),
    "no_verifier": ({**ALL_ON, "verifier": False, "judge_panel": False}, 6),
    "no_retries": ({**ALL_ON, "retries": False, "step_retry": False}, 6),
    "no_degrade": ({**ALL_ON, "degrade": False}, 6),
    "no_self_consistency": ({**ALL_ON, "self_consistency": False}, 6),
    "no_judges": ({**ALL_ON, "judge_panel": False}, 6),
}


@dataclass
class Case:
    case_id: str
    variant: str
    truth: str | None  # P/LP | B/LB | VUS
    split: str = "test"


def offline_cases(n: int, seed: int) -> list[Case]:
    from ..workloads.variant.fixtures import fixtures

    fx = fixtures(seed)
    return [Case(f"syn{i:03d}", v, d["truth"]) for i, (v, d) in enumerate(list(fx.items())[:n])]


def load_cases(path: str, split: str | None = "test", limit: int | None = None) -> list[Case]:
    out = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if split and r.get("split", "test") != split:
            continue
        out.append(Case(r.get("case_id") or r["variant"], r["variant"], acmg.collapse(r.get("clinvar_label")) or r.get("truth"),
                        r.get("split", "test")))
    return out[:limit] if limit else out


def _citation_validity(store: Store, run_id: str) -> tuple[int, int]:
    """Cited functional items whose PMID was retrieved AND whose quote is verbatim.
    Computed from raw step outputs, independent of whether the verifier ran."""
    from ..workloads.variant.workload import _norm

    steps = store.get_steps(run_id)
    lit = (steps.get("literature") or {}).get("output") or {}
    ver = (steps.get("verify") or {}).get("output") or {}
    papers = {p["pmid"]: p for p in lit.get("papers", [])}
    cited = ver.get("functional", [])
    valid = 0
    for it in cited:
        p = papers.get(it["pmid"])
        if p and _norm(it["quote"])[:400] in _norm(p["title"] + " " + p["abstract"]):
            valid += 1
    return valid, len(cited)


def _record(store: Store, run_id: str, case: Case, system: str, profile: str, wall_s: float) -> dict:
    run = store.get_run(run_id)
    res = run.get("result") or {}
    out = res.get("output") or {}
    usage = res.get("usage") or {}
    labels = label_run(store.spans(run_id), store.events(run_id))
    steps = store.get_steps(run_id)
    report = ((steps.get("report") or {}).get("output") or {}).get("summary") or ""
    cls = (steps.get("classify") or {}).get("output") or {}
    blob = json.dumps({"variant": case.variant, "classification": (steps.get("classify") or {}).get("output"),
                       **{k: (steps.get(k) or {}).get("output") for k in ("consequence", "population", "verify")}},
                      default=str)
    valid, cited = _citation_validity(store, run_id)
    faults = sum(v for k, v in labels.items() if k == "fault_injected") + len(store.events(run_id, "crash_injected"))
    injected = sum((usage.get("faults") or {}).get("injected", {}).values()) if usage else 0
    succeeded = run["status"] == RunStatus.SUCCEEDED.value
    label = out.get("classification")
    return {
        "case_id": case.case_id, "variant": case.variant, "truth": case.truth, "system": system, "profile": profile,
        "run_id": run_id, "status": run["status"], "error": run.get("error"), "succeeded": succeeded,
        "label": label, "pred3": acmg.collapse(label) if label and label != "insufficient_evidence" else None,
        "abstained": bool(out.get("abstained")) or label == "insufficient_evidence",
        "points": cls.get("points"), "citations_valid": valid, "citations_total": cited,
        "ungrounded_numbers": len(ungrounded_numbers(report, blob)) if report else 0,
        "degraded": res.get("degraded_steps", []), "faults_injected": max(faults, injected),
        "cost_usd": usage.get("cost_usd", 0.0), "llm_calls": usage.get("llm_calls", 0),
        "tool_calls": usage.get("tool_calls", 0), "wall_s": round(wall_s, 3), "labels": dict(labels),
        "crashed_and_resumed": bool(store.events(run_id, "crash_injected")),
        "steps_redone_after_crash": sum(len(e["data"].get("steps", [])) for e in store.events(run_id, "steps_recovered_after_crash")),
    }


async def run_suite(cases: list[Case], systems: list[str], profiles: list[str], *, mode: str = "offline",
                    seed: int = 0, out_dir: str = "evals/results/latest", settings: Settings | None = None,
                    concurrency: int = 4, recording: str | None = None) -> list[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = Store(str(out / "eval.db"))
    settings = settings or Settings()
    rows: list[dict] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(case: Case, system: str, profile: str) -> dict:
        feats, par = SYSTEMS[system]
        opts = RunOptions(mode=mode, faults=profile, seed=seed, approval_mode="auto_reject", system=system,
                          allow_browser=False, eval_mask_clinvar=True, features=dict(feats),
                          recording=recording or "data/recordings/llm.jsonl")
        rt = Runtime(settings, opts, store=store)
        rt.engine.max_parallel = par
        rid = f"{system}-{profile}-{case.case_id}-s{seed}"
        if any(r["run_id"] == rid for r in store.list_runs(limit=100000)):
            return _record(store, rid, case, system, profile, 0.0)
        async with sem:
            t0 = time.perf_counter()
            try:
                await rt.run("variant", {"variant": case.variant}, run_id=rid)
            except SimulatedCrash:
                # The "process" died. A fresh runtime (new process) resumes it.
                rt2 = Runtime(settings, opts, store=store)
                rt2.engine.max_parallel = par
                if feats.get("step_retry"):
                    await rt2.resume(rid)  # aegis resumes from its checkpoints
                else:
                    # A naive agent keeps no durable state: after a crash it starts over.
                    # Rerun from scratch (same fault rates, crash already "happened")
                    # and charge both attempts' time and cost to the case.
                    from dataclasses import replace

                    from ..core.faults import PROFILES

                    prof = replace(PROFILES[profile], crash_after_step=None, crash_during_step=None)
                    opts2 = replace(opts, faults=prof)
                    rt3 = Runtime(settings, opts2, store=store)
                    rt3.engine.max_parallel = par
                    store.set_run_status(rid, RunStatus.FAILED, error="crashed; restarting from scratch")
                    first = _record(store, rid, case, system, profile, time.perf_counter() - t0)
                    rid2 = rid + "-restart"
                    try:
                        await rt3.run("variant", {"variant": case.variant}, run_id=rid2)
                    except Exception:
                        pass
                    rec = _record(store, rid2, case, system, profile, time.perf_counter() - t0)
                    rec.update(case_id=case.case_id, restarted_from_scratch=True, crashed_and_resumed=False,
                               cost_usd=rec["cost_usd"] + first["cost_usd"],
                               llm_calls=rec["llm_calls"] + first["llm_calls"],
                               tool_calls=rec["tool_calls"] + first["tool_calls"], faults_injected=1 + rec["faults_injected"])
                    return rec
            except Exception as e:  # harness must never die on one case
                try:
                    store.set_run_status(rid, RunStatus.FAILED, error=f"harness: {type(e).__name__}: {e}")
                except Exception:
                    pass
            return _record(store, rid, case, system, profile, time.perf_counter() - t0)

    tasks = [one(c, s, p) for s in systems for p in profiles for c in cases]
    for coro in asyncio.as_completed(tasks):
        rows.append(await coro)
    rows.sort(key=lambda r: (r["system"], r["profile"], r["case_id"]))
    with (out / "runs.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    return rows


def summarize(rows: list[dict], train_rows: list[dict] | None = None) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["system"], r["profile"]), []).append(r)
    summary = {"groups": [], "paired": [], "notes": []}
    for (system, profile), rs in sorted(groups.items()):
        n = len(rs)
        ok = sum(r["succeeded"] for r in rs)
        sr = M.wilson(ok, n)
        answered = [r for r in rs if r["succeeded"] and r["pred3"] and r["truth"]]
        acc = sum(r["pred3"] == r["truth"] for r in answered) / len(answered) if answered else float("nan")
        faulted = [r for r in rs if r["faults_injected"] > 0]
        rec = M.wilson(sum(r["succeeded"] for r in faulted), len(faulted)) if faulted else (float("nan"),) * 3
        cv = sum(r["citations_valid"] for r in rs)
        ct = sum(r["citations_total"] for r in rs)
        binary = [(r["points"], 1 if r["truth"] == "P/LP" else 0) for r in rs
                  if r["succeeded"] and r["points"] is not None and r["truth"] in ("P/LP", "B/LB")]
        au = M.auroc([b[0] for b in binary], [b[1] for b in binary]) if binary else float("nan")
        au_ci = M.bootstrap_ci(M.auroc, [b[0] for b in binary], [b[1] for b in binary]) if len(binary) > 10 else (None, None)
        g = {
            "system": system, "profile": profile, "n": n,
            "success_rate": round(sr[0], 3), "success_ci": [round(sr[1], 3), round(sr[2], 3)],
            "recovery_rate_under_faults": None if not faulted else round(rec[0], 3), "runs_with_faults": len(faulted),
            "accuracy_3class_answered": None if not answered else round(acc, 3),
            "coverage": round(len([r for r in rs if r["succeeded"] and not r["abstained"]]) / n, 3) if n else None,
            "abstain_rate": round(sum(r["abstained"] for r in rs if r["succeeded"]) / max(ok, 1), 3),
            "auroc_plp_vs_blb": None if au != au else round(au, 3),
            "auroc_ci": [None if x is None or x != x else round(x, 3) for x in au_ci],
            "citation_validity": None if ct == 0 else round(cv / ct, 3), "citations_total": ct,
            "reports_with_ungrounded_numbers": sum(1 for r in rs if r["ungrounded_numbers"] > 0),
            "mean_cost_usd": round(sum(r["cost_usd"] for r in rs) / n, 5) if n else None,
            "p50_wall_s": round(M.pct([r["wall_s"] for r in rs], 50), 3),
            "p95_wall_s": round(M.pct([r["wall_s"] for r in rs], 95), 3),
            "mean_llm_calls": round(sum(r["llm_calls"] for r in rs) / n, 2) if n else None,
        }
        if train_rows is not None and binary:
            tr = [(r["points"], 1 if r["truth"] == "P/LP" else 0) for r in train_rows
                  if r["system"] == system and r["profile"] == profile and r["succeeded"] and r["points"] is not None
                  and r["truth"] in ("P/LP", "B/LB")]
            if len(tr) >= 10:
                a, b = M.platt_fit([x[0] for x in tr], [x[1] for x in tr])
                probs = M.platt_apply([x[0] for x in binary], a, b)
                g["calibration"] = {"platt": [round(a, 4), round(b, 4)], "fit_on": "train split",
                                    "ece": round(M.ece(probs, [x[1] for x in binary]), 4),
                                    "brier": round(M.brier(probs, [x[1] for x in binary]), 4)}
        g["coverage_accuracy"] = M.coverage_accuracy([r for r in rs if r["succeeded"]])
        summary["groups"].append(g)
    # Paired significance: baseline vs aegis on the same cases under the same profile.
    by = {(r["system"], r["profile"], r["case_id"]): r for r in rows}
    profiles = sorted({r["profile"] for r in rows})
    for p in profiles:
        ids = sorted({r["case_id"] for r in rows if r["profile"] == p})
        if all(("baseline", p, i) in by and ("aegis", p, i) in by for i in ids) and ids:
            t = M.mcnemar([by[("baseline", p, i)]["succeeded"] for i in ids], [by[("aegis", p, i)]["succeeded"] for i in ids])
            summary["paired"].append({"profile": p, "metric": "run success", "a": "baseline", "b": "aegis", **t})
    return summary


def to_markdown(summary: dict, title: str, mode: str) -> str:
    lines = [f"# {title}", ""]
    if mode == "offline":
        lines += ["> OFFLINE run: synthetic fixtures + deterministic stand-in model with injected hallucinations. "
                  "These numbers measure the runtime (recovery, verification, grounding), NOT real variant "
                  "classification accuracy.", ""]
    cols = ["system", "profile", "n", "success_rate", "success_ci", "recovery_rate_under_faults",
            "citation_validity", "reports_with_ungrounded_numbers", "accuracy_3class_answered", "coverage",
            "auroc_plp_vs_blb", "mean_llm_calls", "mean_cost_usd", "p95_wall_s"]
    if mode == "offline":  # accuracy on synthetic labels is meaningless; do not even show it
        cols = [c for c in cols if c not in ("accuracy_3class_answered", "coverage", "auroc_plp_vs_blb", "mean_cost_usd")]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "---|" * len(cols))
    for g in summary["groups"]:
        lines.append("| " + " | ".join(str(g.get(c)) for c in cols) + " |")
    if summary["paired"]:
        lines += ["", "Paired exact McNemar test on run success (same cases, same faults):", ""]
        for p in summary["paired"]:
            lines.append(f"- {p['profile']}: aegis-only successes {p['only_b_ok']}, baseline-only {p['only_a_ok']}, "
                         f"p = {p['p_value']:.2g}")
    return "\n".join(lines) + "\n"
