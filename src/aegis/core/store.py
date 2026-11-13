"""Durable state: runs, step checkpoints, events, approvals, spans, effects, cache.

SQLite in WAL mode. One file holds everything a run needs to resume after
a crash. Postgres would be the production choice for many workers; the
schema is plain SQL so the swap is mechanical (see docs/DECISIONS.md, ADR-002).

Two guarantees matter most here:

1. Step completion is compare-and-set on the attempt number. If a timed-out
   attempt finishes late, its result is discarded instead of overwriting the
   newer attempt (the stale-result problem).
2. External side effects go through the effects ledger keyed by an
   idempotency key, so a resumed run never sends the same email twice.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .state import RunStatus, StepStatus, check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workload TEXT NOT NULL,
    input_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    output_json TEXT,
    error TEXT,
    started_at REAL,
    finished_at REAL,
    PRIMARY KEY (run_id, step_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    args_json TEXT NOT NULL,
    risk TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT,
    created_at REAL NOT NULL,
    decided_at REAL,
    UNIQUE (run_id, step_id, tool, args_hash)
);
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_id TEXT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL,
    status TEXT,
    attrs_json TEXT
);
CREATE INDEX IF NOT EXISTS spans_run ON spans(run_id);
CREATE TABLE IF NOT EXISTS effects (
    idem_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def args_hash(args: Any) -> str:
    return hashlib.sha256(_dumps(args).encode()).hexdigest()[:16]


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ utils
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self._exec(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._exec(sql, params).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------- runs
    def create_run(self, workload: str, run_input: dict, run_id: str | None = None) -> str:
        run_id = run_id or uuid.uuid4().hex[:12]
        now = time.time()
        self._exec(
            "INSERT INTO runs(run_id, workload, input_json, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, workload, _dumps(run_input), RunStatus.PENDING.value, now, now),
        )
        self.add_event(run_id, "run_created", {"workload": workload})
        return run_id

    def get_run(self, run_id: str) -> dict:
        row = self._one("SELECT * FROM runs WHERE run_id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        row["input"] = json.loads(row.pop("input_json"))
        rj = row.pop("result_json")
        row["result"] = json.loads(rj) if rj else None
        return row

    def list_runs(self, limit: int = 50, workload: str | None = None) -> list[dict]:
        if workload:
            rows = self._all(
                "SELECT run_id, workload, status, error, created_at, updated_at FROM runs WHERE workload=? ORDER BY created_at DESC LIMIT ?",
                (workload, limit),
            )
        else:
            rows = self._all(
                "SELECT run_id, workload, status, error, created_at, updated_at FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return rows

    def set_run_status(
        self, run_id: str, status: RunStatus, *, result: Any = None, error: str | None = None
    ) -> None:
        with self._lock:
            cur = RunStatus(self.get_run(run_id)["status"])
            check_transition(cur, status)
            self._exec(
                "UPDATE runs SET status=?, result_json=COALESCE(?, result_json), error=?, updated_at=? WHERE run_id=?",
                (status.value, _dumps(result) if result is not None else None, error, time.time(), run_id),
            )
        self.add_event(run_id, "run_status", {"from": cur.value, "to": status.value, "error": error})

    def request_cancel(self, run_id: str) -> None:
        self._exec("UPDATE runs SET cancel_requested=1, updated_at=? WHERE run_id=?", (time.time(), run_id))
        self.add_event(run_id, "cancel_requested", {})

    def is_cancel_requested(self, run_id: str) -> bool:
        row = self._one("SELECT cancel_requested FROM runs WHERE run_id=?", (run_id,))
        return bool(row and row["cancel_requested"])

    def runs_in_status(self, status: RunStatus) -> list[str]:
        return [r["run_id"] for r in self._all("SELECT run_id FROM runs WHERE status=?", (status.value,))]

    # ------------------------------------------------------------------ steps
    def get_steps(self, run_id: str) -> dict[str, dict]:
        out = {}
        for r in self._all("SELECT * FROM steps WHERE run_id=?", (run_id,)):
            oj = r.pop("output_json")
            r["output"] = json.loads(oj) if oj else None
            out[r["step_id"]] = r
        return out

    def begin_step(self, run_id: str, step_id: str) -> int:
        """Mark a step RUNNING and return the new attempt number."""
        with self._lock:
            now = time.time()
            self._exec(
                """INSERT INTO steps(run_id, step_id, status, attempts, started_at) VALUES (?,?,?,1,?)
                   ON CONFLICT(run_id, step_id) DO UPDATE SET status=excluded.status,
                   attempts=steps.attempts+1, started_at=excluded.started_at, error=NULL""",
                (run_id, step_id, StepStatus.RUNNING.value, now),
            )
            row = self._one("SELECT attempts FROM steps WHERE run_id=? AND step_id=?", (run_id, step_id))
        return int(row["attempts"])

    def complete_step(self, run_id: str, step_id: str, attempt: int, output: Any) -> bool:
        """Compare-and-set: only the current attempt may write a result."""
        cur = self._exec(
            """UPDATE steps SET status=?, output_json=?, finished_at=?
               WHERE run_id=? AND step_id=? AND attempts=? AND status=?""",
            (StepStatus.SUCCEEDED.value, _dumps(output), time.time(), run_id, step_id, attempt, StepStatus.RUNNING.value),
        )
        ok = cur.rowcount == 1
        if not ok:
            self.add_event(run_id, "stale_result_discarded", {"step": step_id, "attempt": attempt})
        return ok

    def set_step(self, run_id: str, step_id: str, status: StepStatus, *, error: str | None = None,
                 output: Any = None) -> None:
        with self._lock:
            self._exec(
                """INSERT INTO steps(run_id, step_id, status, attempts, output_json, error, finished_at)
                   VALUES (?,?,?,0,?,?,?)
                   ON CONFLICT(run_id, step_id) DO UPDATE SET status=excluded.status,
                   error=excluded.error, finished_at=excluded.finished_at,
                   output_json=COALESCE(excluded.output_json, steps.output_json)""",
                (run_id, step_id, status.value, _dumps(output) if output is not None else None, error, time.time()),
            )

    # ----------------------------------------------------------------- events
    def add_event(self, run_id: str, kind: str, data: dict | None = None) -> None:
        self._exec(
            "INSERT INTO events(run_id, ts, kind, data_json) VALUES (?,?,?,?)",
            (run_id, time.time(), kind, _dumps(data or {})),
        )

    def events(self, run_id: str, kind: str | None = None) -> list[dict]:
        if kind:
            rows = self._all("SELECT * FROM events WHERE run_id=? AND kind=? ORDER BY id", (run_id, kind))
        else:
            rows = self._all("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))
        for r in rows:
            r["data"] = json.loads(r.pop("data_json") or "{}")
        return rows

    # -------------------------------------------------------------- approvals
    def get_or_create_approval(self, run_id: str, step_id: str, tool: str, args: dict, risk: str,
                               reason: str) -> dict:
        h = args_hash(args)
        with self._lock:
            row = self._one(
                "SELECT * FROM approvals WHERE run_id=? AND step_id=? AND tool=? AND args_hash=?",
                (run_id, step_id, tool, h),
            )
            if row:
                return row
            self._exec(
                """INSERT INTO approvals(run_id, step_id, tool, args_hash, args_json, risk, reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, step_id, tool, h, _dumps(args), risk, reason, time.time()),
            )
            row = self._one(
                "SELECT * FROM approvals WHERE run_id=? AND step_id=? AND tool=? AND args_hash=?",
                (run_id, step_id, tool, h),
            )
        self.add_event(run_id, "approval_requested", {"approval_id": row["id"], "tool": tool, "risk": risk})
        return row

    def decide_approval(self, approval_id: int, approved: bool, by: str = "human") -> dict:
        status = "approved" if approved else "rejected"
        self._exec(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
            (status, by, time.time(), approval_id),
        )
        row = self._one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if row is None:
            raise KeyError(approval_id)
        self.add_event(row["run_id"], "approval_decided", {"approval_id": approval_id, "status": row["status"]})
        return row

    def list_approvals(self, status: str | None = "pending", run_id: str | None = None) -> list[dict]:
        q, p = "SELECT * FROM approvals WHERE 1=1", []
        if status:
            q += " AND status=?"
            p.append(status)
        if run_id:
            q += " AND run_id=?"
            p.append(run_id)
        rows = self._all(q + " ORDER BY id", tuple(p))
        for r in rows:
            r["args"] = json.loads(r.pop("args_json"))
        return rows

    # ------------------------------------------------------------------ spans
    def write_span(self, span: dict) -> None:
        self._exec(
            """INSERT OR REPLACE INTO spans(span_id, run_id, parent_id, name, kind, start, end, status, attrs_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (span["span_id"], span["run_id"], span.get("parent_id"), span["name"], span["kind"],
             span["start"], span.get("end"), span.get("status"), _dumps(span.get("attrs", {}))),
        )

    def spans(self, run_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM spans WHERE run_id=? ORDER BY start", (run_id,))
        for r in rows:
            r["attrs"] = json.loads(r.pop("attrs_json") or "{}")
        return rows

    # ---------------------------------------------------------------- effects
    def get_effect(self, idem_key: str) -> Any | None:
        row = self._one("SELECT result_json FROM effects WHERE idem_key=?", (idem_key,))
        return json.loads(row["result_json"]) if row else None

    def put_effect(self, idem_key: str, run_id: str, result: Any) -> bool:
        cur = self._exec(
            "INSERT OR IGNORE INTO effects(idem_key, run_id, result_json, created_at) VALUES (?,?,?,?)",
            (idem_key, run_id, _dumps(result), time.time()),
        )
        return cur.rowcount == 1

    # ------------------------------------------------------------------ cache
    def cache_get(self, key: str) -> dict | None:
        row = self._one("SELECT response_json FROM llm_cache WHERE key=?", (key,))
        return json.loads(row["response_json"]) if row else None

    def cache_put(self, key: str, value: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO llm_cache(key, response_json, created_at) VALUES (?,?,?)",
            (key, _dumps(value), time.time()),
        )
