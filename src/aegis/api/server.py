"""HTTP API + live trace explorer.

    uvicorn aegis.api.server:app --port 8080      (or: aegis serve)

On startup the server resumes every run left RUNNING by a previous process
(the crash-recovery path, exercised for real by killing the server).
Prometheus metrics at /metrics.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from ..config import Settings
from ..core.state import RunStatus
from ..services import RunOptions, Runtime
from ..tracing.taxonomy import label_run

settings = Settings()
MODE = os.environ.get("AEGIS_MODE") or ("live" if settings.has_llm() else "offline")
runtime = Runtime(settings, RunOptions(mode=MODE, faults=os.environ.get("AEGIS_FAULTS", "none")))
app = FastAPI(title="Aegis", version="0.3.0")
_tasks: dict[str, asyncio.Task] = {}


def _spawn(coro, run_id: str) -> None:
    async def wrap():
        try:
            await coro
        except Exception as e:  # never let a background run kill the server
            try:
                runtime.store.set_run_status(run_id, RunStatus.FAILED, error=f"{type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            _tasks.pop(run_id, None)

    _tasks[run_id] = asyncio.create_task(wrap())


@app.on_event("startup")
async def recover_interrupted_runs() -> None:
    for rid in runtime.store.runs_in_status(RunStatus.RUNNING):
        runtime.store.add_event(rid, "server_restart_resume", {})
        _spawn(runtime.resume(rid), rid)


class StartRun(BaseModel):
    workload: str
    input: dict


class Decision(BaseModel):
    approved: bool
    by: str = "human"


@app.get("/", response_class=HTMLResponse)
def product_app() -> str:
    return (Path(__file__).parent / "app.html").read_text().replace("{{MODE}}", MODE)


@app.get("/explorer", response_class=HTMLResponse)
def explorer() -> str:
    return (Path(__file__).parent / "explorer.html").read_text().replace("{{MODE}}", MODE)


# ------------------------------------------------------------------ reports
@app.get("/reports/run/{run_id}", response_class=HTMLResponse)
def run_report(run_id: str) -> str:
    from ..reports.variant_report import render

    try:
        return render(runtime.store, run_id)
    except KeyError:
        raise HTTPException(404)


# ------------------------------------------------------------------ batches
@app.post("/api/batches")
async def create_batch(file: UploadFile = File(...), name: str = Form(""), phenotype: str = Form("")):
    import re
    import tempfile

    from ..batch import finish_batch, parse_input, register_batch, run_batch

    raw = await file.read()
    if len(raw) > 50_000_000:
        raise HTTPException(413, "file too large (50 MB max)")
    suffix = "".join(Path(file.filename or "upload.vcf").suffixes) or ".vcf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(raw)
    parsed = parse_input(tmp.name)
    if not parsed.variants:
        raise HTTPException(400, f"no usable variants; skipped {len(parsed.skipped)}: {parsed.skipped[:5]}")
    name = re.sub(r"[^A-Za-z0-9_-]", "_", name or Path(file.filename or "batch").stem)[:40] + "-" + str(int(time.time()))[-6:]
    out = str(Path(settings.db_path).parent / "batches" / name)
    register_batch(runtime.store, name, file.filename or "", parsed, out)

    async def go():
        summary = await run_batch(runtime, parsed.variants, name, out, concurrency=3, phenotype=phenotype,
                                  progress=lambda *_: None)
        finish_batch(runtime.store, name, summary)

    _spawn(go(), f"batch:{name}")
    return {"name": name, "variants": len(parsed.variants), "skipped": parsed.skipped[:50]}


@app.get("/api/batches")
def batches():
    from ..batch import list_batches

    out = []
    for b in list_batches(runtime.store):
        statuses = {}
        from ..batch import run_id_for

        for v in b["variants"]:
            try:
                st = runtime.store.get_run(run_id_for(b["name"], v))["status"]
            except KeyError:
                st = "queued"
            statuses[st] = statuses.get(st, 0) + 1
        out.append({"name": b["name"], "created_at": b["created_at"], "source": b["source"], "status": b["status"],
                    "variants": len(b["variants"]), "skipped": len(b["skipped"]), "progress": statuses,
                    "summary": b["summary"]})
    return out


@app.get("/batches/{name}", response_class=HTMLResponse)
def batch_page_live(name: str) -> str:
    from ..batch import batch_rows
    from ..reports.variant_report import batch_page

    try:
        rows = batch_rows(runtime.store, name)
    except KeyError:
        raise HTTPException(404)
    counts: dict[str, int] = {}
    for r in rows:
        if r["status"] == "succeeded":
            r["report"] = f"/reports/run/{r['run_id']}"
        k = r["classification"] or ("failed" if r["status"] == "failed" else r["status"])
        counts[k] = counts.get(k, 0) + 1
    return batch_page(f"Batch {name}", rows, counts)


@app.get("/api/batches/{name}/results.tsv", response_class=PlainTextResponse)
def batch_tsv(name: str) -> str:
    from ..batch import batch_rows

    rows = batch_rows(runtime.store, name)
    cols = ["variant", "gene", "consequence", "hgvsc", "hgvsp", "gnomad_faf95", "revel", "classification", "points",
            "criteria", "citations", "clinvar", "status", "run_id"]
    return "\t".join(cols) + "\n" + "\n".join("\t".join("" if r.get(c) is None else str(r.get(c)) for c in cols) for r in rows) + "\n"


# ---------------------------------------------------------------- watchlist
class WatchAdd(BaseModel):
    variant: str
    note: str = ""


@app.get("/api/watch")
def watch_list():
    from ..watch import Watchlist

    w = Watchlist(runtime.store)
    return {"watching": w.list(), "changes": w.changes(50)}


@app.post("/api/watch")
def watch_add(body: WatchAdd):
    from ..watch import Watchlist

    try:
        Watchlist(runtime.store).add(body.variant, body.note)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/watch/{variant}")
def watch_remove(variant: str):
    from ..watch import Watchlist

    Watchlist(runtime.store).remove(variant)
    return {"ok": True}


@app.post("/api/watch/run")
async def watch_run():
    from ..watch import Watchlist, write_digest

    async def go():
        rep = await Watchlist(runtime.store).run(runtime, progress=lambda *_: None)
        write_digest(rep, str(Path(settings.db_path).parent / "watch"))

    _spawn(go(), "watch:run")
    return {"started": True}


@app.get("/compare", response_class=HTMLResponse)
def compare_page() -> str:
    for p in (Path("evals/results/compare/compare.html"), Path(settings.db_path).parent / "compare" / "compare.html"):
        if p.exists():
            return p.read_text()
    return "<p style='font-family:sans-serif'>No comparison yet. Run <code>aegis compare</code>.</p>"


@app.get("/api/runs")
def runs(limit: int = 100):
    out = runtime.store.list_runs(limit)
    for r in out:
        r["active"] = r["run_id"] in _tasks
    return out


@app.post("/api/runs")
async def start(body: StartRun):
    if body.workload not in runtime.workloads.WORKLOADS:
        raise HTTPException(400, f"unknown workload {body.workload}")
    inp = {**body.input, "_features": dict(runtime.options.features)}
    rid = runtime.store.create_run(body.workload, inp)
    _spawn(runtime.engine.execute(rid), rid)
    return {"run_id": rid}


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    try:
        r = runtime.store.get_run(run_id)
    except KeyError:
        raise HTTPException(404)
    r["steps"] = runtime.store.get_steps(run_id)
    r["labels"] = label_run(runtime.store.spans(run_id), runtime.store.events(run_id))
    r["approvals"] = runtime.store.list_approvals(None, run_id)
    return r


@app.get("/api/runs/{run_id}/spans")
def spans(run_id: str):
    return runtime.store.spans(run_id)


@app.get("/api/runs/{run_id}/events")
def events(run_id: str):
    return runtime.store.events(run_id)


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: str):
    runtime.engine.cancel(run_id)
    return {"ok": True}


@app.post("/api/runs/{run_id}/resume")
async def resume(run_id: str):
    if run_id in _tasks:
        raise HTTPException(409, "run is active")
    _spawn(runtime.resume(run_id), run_id)
    return {"ok": True}


@app.get("/api/approvals")
def approvals(status: str | None = "pending"):
    return runtime.store.list_approvals(status)


@app.post("/api/approvals/{approval_id}")
async def decide(approval_id: int, d: Decision):
    row = runtime.store.decide_approval(approval_id, d.approved, d.by)
    rid = row["run_id"]
    if runtime.store.get_run(rid)["status"] == RunStatus.WAITING_APPROVAL.value and rid not in _tasks:
        _spawn(runtime.resume(rid), rid)
    return row


@app.get("/api/memory")
def memory(status: str | None = None):
    return runtime.memory().list(status)


@app.post("/api/memory/{lesson_id}/{status}")
def set_memory(lesson_id: str, status: str):
    if status not in ("active", "candidate", "retired"):
        raise HTTPException(400)
    return {"ok": runtime.memory().set_status(lesson_id, status)}


@app.get("/api/evals/latest")
def eval_latest():
    p = Path("evals/results/latest/summary.json")
    return json.loads(p.read_text()) if p.exists() else {}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    st = runtime.store
    lines = ["# TYPE aegis_runs gauge"]
    for row in st._all("SELECT workload, status, COUNT(*) n FROM runs GROUP BY workload, status"):
        lines.append(f'aegis_runs{{workload="{row["workload"]}",status="{row["status"]}"}} {row["n"]}')
    lines.append("# TYPE aegis_events_total counter")
    for row in st._all("SELECT kind, COUNT(*) n FROM events GROUP BY kind"):
        lines.append(f'aegis_events_total{{kind="{row["kind"]}"}} {row["n"]}')
    lines.append("# TYPE aegis_llm_cost_usd_total counter")
    total = sum(json.loads(r["attrs_json"] or "{}").get("cost_usd", 0) for r in
                st._all("SELECT attrs_json FROM spans WHERE kind='llm'"))
    lines.append(f"aegis_llm_cost_usd_total {total:.6f}")
    lines.append(f"aegis_pending_approvals {len(st.list_approvals('pending'))}")
    return "\n".join(lines) + "\n"
