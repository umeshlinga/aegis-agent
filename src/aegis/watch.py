"""Variant watchlist: re-curate on a schedule and report what changed.

Evidence moves: new papers appear, ClinVar submitters reclassify, gnomAD
releases add carriers. A call made in March can be wrong in November.

    aegis watch add 17-43045712-T-C --note "proband 042, BRCA1"
    aegis watch run            # re-curates everything, diffs against the last snapshot
    aegis watch changes        # history of what changed and when

Schedule `aegis watch run` weekly with launchd or cron (docs/RUNBOOK.md).
A change is: classification, total points, the set of criteria, the set of
verified citations, or ClinVar's classification. Each change is stored with
both run ids, so the before/after reports can be opened side by side.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    variant TEXT PRIMARY KEY, note TEXT, added_at REAL NOT NULL,
    last_run_id TEXT, last_snapshot_json TEXT, last_checked REAL
);
CREATE TABLE IF NOT EXISTS watch_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, variant TEXT NOT NULL, ts REAL NOT NULL,
    old_run_id TEXT, new_run_id TEXT, changes_json TEXT NOT NULL
);
"""


class Watchlist:
    def __init__(self, store):
        self.store = store
        with store._lock:
            store._conn.executescript(SCHEMA)

    def add(self, variant: str, note: str = "") -> None:
        from .tools.bio import parse_variant

        v = parse_variant(variant).gnomad_id
        self.store._exec("INSERT OR IGNORE INTO watchlist(variant, note, added_at) VALUES (?,?,?)", (v, note, time.time()))

    def remove(self, variant: str) -> None:
        self.store._exec("DELETE FROM watchlist WHERE variant=?", (variant,))

    def list(self) -> list[dict]:
        rows = self.store._all("SELECT * FROM watchlist ORDER BY added_at")
        for r in rows:
            r["last"] = json.loads(r.pop("last_snapshot_json") or "null")
        return rows

    def changes(self, limit: int = 100) -> list[dict]:
        rows = self.store._all("SELECT * FROM watch_changes ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["changes"] = json.loads(r.pop("changes_json"))
        return rows

    def snapshot(self, run_id: str) -> dict:
        steps = {k: (v.get("output") or {}) for k, v in self.store.get_steps(run_id).items()}
        cls = steps.get("classify", {})
        cv = steps.get("clinical", {})
        return {
            "status": self.store.get_run(run_id)["status"],
            "label": cls.get("label"),
            "points": cls.get("points"),
            "criteria": sorted(c["code"] for c in cls.get("criteria") or []),
            "citations": sorted(f["pmid"] for f in (steps.get("verify") or {}).get("functional", [])),
            "clinvar": cv.get("classification") if cv.get("found") else None,
            "papers_seen": sorted(p["pmid"] for p in (steps.get("literature") or {}).get("papers", [])),
        }

    @staticmethod
    def diff(old: dict | None, new: dict) -> list[dict]:
        if old is None:
            return []
        out = []
        if new["status"] != "succeeded":
            return [{"field": "status", "old": old.get("status"), "new": new["status"],
                     "note": "re-check failed; previous call kept"}]
        for f in ("label", "points", "clinvar"):
            if old.get(f) != new.get(f):
                out.append({"field": f, "old": old.get(f), "new": new.get(f)})
        for f in ("criteria", "citations"):
            a, b = set(old.get(f) or []), set(new.get(f) or [])
            if a != b:
                out.append({"field": f, "added": sorted(b - a), "removed": sorted(a - b)})
        new_papers = sorted(set(new.get("papers_seen") or []) - set(old.get("papers_seen") or []))
        if new_papers:
            out.append({"field": "new_papers", "added": new_papers})
        return out

    async def run(self, runtime, progress=print) -> dict:
        today = time.strftime("%Y%m%d-%H%M")
        report = {"checked": 0, "changed": [], "failed": []}
        for w in self.list():
            v = w["variant"]
            rid = f"w-{hashlib.sha1(v.encode()).hexdigest()[:10]}-{today}"
            try:
                runtime.store.get_run(rid)
                await runtime.resume(rid)
            except KeyError:
                await runtime.run("variant", {"variant": v, "use_browser": False, "_watch": True}, run_id=rid)
            snap = self.snapshot(rid)
            changes = self.diff(w["last"], snap)
            report["checked"] += 1
            if snap["status"] != "succeeded":
                report["failed"].append(v)
                progress(f"  {v}: re-check {snap['status']} (previous call kept)")
                continue
            if changes:
                self.store._exec("INSERT INTO watch_changes(variant, ts, old_run_id, new_run_id, changes_json) VALUES (?,?,?,?,?)",
                                 (v, time.time(), w["last_run_id"], rid, json.dumps(changes)))
                report["changed"].append({"variant": v, "note": w["note"], "changes": changes,
                                          "old_run": w["last_run_id"], "new_run": rid})
            self.store._exec("UPDATE watchlist SET last_run_id=?, last_snapshot_json=?, last_checked=? WHERE variant=?",
                             (rid, json.dumps(snap), time.time(), v))
            first = w["last"] is None
            progress(f"  {v}: {snap['label']} ({snap['points']} pts)" +
                     (" [baseline recorded]" if first else f" [{len(changes)} change(s)]" if changes else " [no change]"))
        return report


def digest_markdown(report: dict) -> str:
    lines = [f"# Aegis watchlist digest {time.strftime('%Y-%m-%d')}", "",
             f"Checked {report['checked']} variants; {len(report['changed'])} changed; {len(report['failed'])} re-checks failed.", ""]
    for c in report["changed"]:
        lines.append(f"## {c['variant']} {('· ' + c['note']) if c['note'] else ''}")
        for ch in c["changes"]:
            if "old" in ch:
                lines.append(f"- {ch['field']}: {ch['old']} → {ch['new']}")
            else:
                lines.append(f"- {ch['field']}: +{ch.get('added', [])} −{ch.get('removed', [])}")
        lines.append(f"- runs: `{c['old_run']}` → `{c['new_run']}` (aegis report <run_id>)")
        lines.append("")
    for v in report["failed"]:
        lines.append(f"- re-check failed for {v}; previous call kept")
    return "\n".join(lines) + "\n"


def write_digest(report: dict, out_dir: str = "data/watch") -> str:
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    path = p / f"digest-{time.strftime('%Y%m%d-%H%M')}.md"
    path.write_text(digest_markdown(report))
    return str(path)
