"""Batch curation: a VCF or a variant list in, a results table and reports out.

    aegis batch my_variants.vcf --name proband_042 --concurrency 4

* VCF (.vcf / .vcf.gz): multi-allelic sites are split, symbolic/structural
  alleles (<DEL>, *, breakends) are skipped and listed, FILTER != PASS is
  skipped unless --include-filtered, "chr" prefixes are normalized.
* CSV/TSV: a `variant` column (CHROM-POS-REF-ALT) or chrom,pos,ref,alt columns.

Every variant is its own durable run with a deterministic id derived from the
batch name and the variant, so re-running the same command resumes: finished
variants are skipped, failed or interrupted ones are resumed, new ones start.
Concurrency is bounded and NCBI/Ensembl/gnomAD rate limits are shared across
all runs in the process.
"""
from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .core.errors import SimulatedCrash
from .core.state import RunStatus
from .tools.bio import parse_variant


@dataclass
class ParsedInput:
    variants: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (raw, reason)


def _open(path: Path):
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"))
    return path.open()


def parse_vcf(path: Path, include_filtered: bool = False) -> ParsedInput:
    out = ParsedInput()
    seen = set()
    with _open(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 5:
                out.skipped.append((line.strip()[:80], "fewer than 5 columns"))
                continue
            chrom, pos, _id, ref, alts = cols[:5]
            filt = cols[6] if len(cols) > 6 else "PASS"
            chrom = re.sub(r"^chr", "", chrom, flags=re.I)
            if chrom in ("M", "MT"):
                out.skipped.append((f"{chrom}:{pos}", "mitochondrial (not supported)"))
                continue
            if not include_filtered and filt not in ("PASS", ".", ""):
                out.skipped.append((f"{chrom}:{pos}", f"FILTER={filt}"))
                continue
            for alt in alts.split(","):
                raw = f"{chrom}-{pos}-{ref}-{alt}"
                if alt.startswith("<") or alt in ("*", ".") or "[" in alt or "]" in alt:
                    out.skipped.append((raw, "symbolic/structural allele"))
                    continue
                try:
                    v = parse_variant(raw).gnomad_id
                except Exception as e:
                    out.skipped.append((raw, str(e)[:80]))
                    continue
                if v not in seen:
                    seen.add(v)
                    out.variants.append(v)
    return out


def parse_table(path: Path) -> ParsedInput:
    out = ParsedInput()
    seen = set()
    text = path.read_text()
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    for row in csv.DictReader(io.StringIO(text), dialect=dialect):
        low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        raw = low.get("variant") or "-".join(low.get(k, "") for k in ("chrom", "pos", "ref", "alt"))
        try:
            v = parse_variant(raw).gnomad_id
        except Exception as e:
            out.skipped.append((raw, str(e)[:80]))
            continue
        if v not in seen:
            seen.add(v)
            out.variants.append(v)
    return out


def parse_input(path: str, include_filtered: bool = False) -> ParsedInput:
    p = Path(path)
    name = p.name.lower()
    if name.endswith((".vcf", ".vcf.gz")):
        return parse_vcf(p, include_filtered)
    return parse_table(p)


def run_id_for(batch: str, variant: str) -> str:
    return f"b-{re.sub(r'[^A-Za-z0-9_-]', '_', batch)[:40]}-{hashlib.sha1(variant.encode()).hexdigest()[:10]}"


def _row(store, run_id: str, variant: str) -> dict:
    try:
        run = store.get_run(run_id)
    except KeyError:
        return {"variant": variant, "status": "missing", "run_id": run_id}
    steps = {k: (v.get("output") or {}) for k, v in store.get_steps(run_id).items()}
    con, pop, cls = steps.get("consequence", {}), steps.get("population", {}), steps.get("classify", {})
    cv = steps.get("clinical", {})
    out = (run.get("result") or {}).get("output") or {}
    usage = (run.get("result") or {}).get("usage") or {}
    return {
        "variant": variant, "status": run["status"], "run_id": run_id,
        "gene": con.get("gene_symbol"), "consequence": con.get("most_severe_consequence"),
        "hgvsc": con.get("hgvsc"), "hgvsp": con.get("hgvsp"), "hgvsp_short": con.get("protein_short"),
        "gnomad_faf95": pop.get("faf95_popmax"), "gnomad_ac": pop.get("ac"), "revel": con.get("revel"),
        "classification": cls.get("label") if run["status"] == "succeeded" else None,
        "points": cls.get("points"), "criteria": " ".join(c["code"] for c in cls.get("criteria") or []),
        "citations": " ".join(out.get("citations") or []),
        "clinvar": cv.get("classification") if cv.get("found") else None,
        "clinvar_agrees": cls.get("clinvar_concordant"),
        "degraded": " ".join((run.get("result") or {}).get("degraded_steps") or []),
        "cost_usd": usage.get("cost_usd"), "error": run.get("error"),
    }


async def run_batch(runtime, variants: list[str], batch: str, out_dir: str, concurrency: int = 4,
                    phenotype: str = "", use_browser: bool = False, progress=print) -> dict:
    from .reports.variant_report import batch_page, render

    store = runtime.store
    out = Path(out_dir)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    done = Counter()

    async def one(v: str) -> None:
        rid = run_id_for(batch, v)
        async with sem:
            try:
                existing = store.get_run(rid)
            except KeyError:
                existing = None
            try:
                if existing is None:
                    await runtime.run("variant", {"variant": v, "phenotype": phenotype, "use_browser": use_browser,
                                                  "_batch": batch}, run_id=rid)
                    done["new"] += 1
                elif existing["status"] in ("succeeded", "cancelled"):
                    done["already_done"] += 1
                else:
                    await runtime.resume(rid)
                    done["resumed"] += 1
            except SimulatedCrash:
                raise
            except Exception as e:
                try:
                    store.set_run_status(rid, RunStatus.FAILED, error=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
                done["errored"] += 1
            st = store.get_run(rid)["status"]
            progress(f"  [{sum(done.values())}/{len(variants)}] {v}: {st}")

    await asyncio.gather(*(one(v) for v in variants))
    rows = [_row(store, run_id_for(batch, v), v) for v in variants]
    for r in rows:
        if r["status"] == "succeeded":
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", r["variant"]) + ".html"
            (out / "reports" / name).write_text(render(store, r["run_id"]))
            r["report"] = f"reports/{name}"
    cols = ["variant", "gene", "consequence", "hgvsc", "hgvsp", "gnomad_faf95", "gnomad_ac", "revel", "classification",
            "points", "criteria", "citations", "clinvar", "clinvar_agrees", "degraded", "status", "error", "cost_usd",
            "run_id", "report"]
    with (out / "results.tsv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    counts = Counter((r["classification"] or ("failed" if r["status"] != "succeeded" else "?")) for r in rows)
    (out / "index.html").write_text(batch_page(f"Batch {batch}", rows, dict(counts)))
    summary = {"batch": batch, "variants": len(variants), "progress": dict(done), "classifications": dict(counts),
               "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in rows), 4),
               "results": str(out / "results.tsv"), "report": str(out / "index.html")}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


# ------------------------------------------------------------- batch registry
BATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    name TEXT PRIMARY KEY, created_at REAL NOT NULL, source TEXT, variants_json TEXT NOT NULL,
    skipped_json TEXT, status TEXT NOT NULL DEFAULT 'running', summary_json TEXT, out_dir TEXT
);
"""


def _ensure(store) -> None:
    with store._lock:
        store._conn.executescript(BATCH_SCHEMA)


def register_batch(store, name: str, source: str, parsed: ParsedInput, out_dir: str) -> None:
    import time

    _ensure(store)
    store._exec("INSERT OR REPLACE INTO batches(name, created_at, source, variants_json, skipped_json, status, out_dir) "
                "VALUES (?,?,?,?,?,?,?)", (name, time.time(), source, json.dumps(parsed.variants),
                                          json.dumps(parsed.skipped), "running", out_dir))


def finish_batch(store, name: str, summary: dict) -> None:
    _ensure(store)
    store._exec("UPDATE batches SET status='done', summary_json=? WHERE name=?", (json.dumps(summary), name))


def list_batches(store) -> list[dict]:
    _ensure(store)
    rows = store._all("SELECT * FROM batches ORDER BY created_at DESC")
    for r in rows:
        r["variants"] = json.loads(r.pop("variants_json"))
        r["skipped"] = json.loads(r.pop("skipped_json") or "[]")
        r["summary"] = json.loads(r.pop("summary_json") or "null")
    return rows


def batch_rows(store, name: str) -> list[dict]:
    b = next((x for x in list_batches(store) if x["name"] == name), None)
    if b is None:
        raise KeyError(name)
    return [_row(store, run_id_for(name, v), v) for v in b["variants"]]
