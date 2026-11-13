"""Explorer data layer: read trace files, group events into runs, and
shape each event for display.

Runs carry a mode (recovery on or off) only when the run's producer
wrote a manifest next to the trace file (runs.json, mapping run id to
metadata). The eval harness writes traces without a manifest, so for
those files the mode is reported as "not recorded" rather than guessed.
The demo trace generator writes the manifest, so the explorer's default
data set always has real modes.

Permission scopes are not stored in tool_call events, so the explorer
rebuilds the tool declarations from the same factories the runtime
uses and looks scopes up by tool name. Building the browser tool specs
does not launch a browser: BrowserSession starts lazily, on first use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..tools.browser import BrowserSession, make_browser_tools
from ..tools.registry import ToolSpec
from ..tools.stubs import NoteStore, make_charge_tool, make_note_tools
from ..trace.store import read_events

GATED_KINDS = {"permission_denied"}


def tool_scopes() -> dict[str, dict[str, Any]]:
    """Tool name -> {"scope": str, "irreversible": bool}, from the real
    tool factories."""
    specs: list[ToolSpec] = []
    specs.extend(make_note_tools(NoteStore()))
    specs.append(make_charge_tool([]))
    specs.extend(make_browser_tools(BrowserSession("http://127.0.0.1")))
    return {
        spec.name: {
            "scope": spec.scope.value,
            "irreversible": bool(spec.irreversible),
            "needs_approval": bool(spec.needs_approval),
        }
        for spec in specs
    }


def load_manifest(trace_path: str | Path) -> dict[str, dict[str, Any]]:
    manifest_path = Path(trace_path).parent / "runs.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def group_runs(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    runs: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        runs.setdefault(event.get("run_id", ""), []).append(event)
    for run_events in runs.values():
        run_events.sort(key=lambda e: (e.get("ts", 0), e.get("seq", 0)))
    return runs


def summarize_run(
    run_id: str,
    events: list[dict[str, Any]],
    manifest: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    started = next((e for e in events if e["type"] == "run_started"), None)
    finished = next((e for e in events if e["type"] == "run_finished"), None)
    payload = (started or {}).get("payload", {})
    meta = manifest.get(run_id, {})
    first_ts = events[0]["ts"] if events else 0.0
    last_ts = events[-1]["ts"] if events else first_ts
    recovery = [e for e in events if e["type"] == "recovery_event"]
    gates = [
        e for e in recovery if e["payload"].get("failure_kind") in GATED_KINDS
    ]
    return {
        "run_id": run_id,
        "task_id": meta.get("task_id") or payload.get("task_id") or "unknown task",
        "task_text": payload.get("task_text", ""),
        "mode": meta.get("mode", "not recorded"),
        "outcome": (finished or {}).get("payload", {}).get("status", "incomplete"),
        "started_at": _iso(first_ts) if events else "",
        "started_ts": round(first_ts, 3),
        "duration_s": round(last_ts - first_ts, 3),
        "event_count": len(events),
        "recovery_events": len(recovery),
        "gate_events": len(gates),
    }


def list_runs(
    trace_path: str | Path, manifest: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    if manifest is None:
        manifest = load_manifest(trace_path)
    events = list(read_events(trace_path))
    runs = group_runs(events)
    summaries = [
        summarize_run(run_id, run_events, manifest)
        for run_id, run_events in runs.items()
    ]
    summaries.sort(key=lambda s: s["started_ts"], reverse=True)
    return summaries


def _fmt_cost(cost: Any, is_estimate: Any) -> str:
    try:
        text = f"${float(cost):.4f}"
    except (TypeError, ValueError):
        text = "$0.0000"
    return text + (" (estimate)" if is_estimate else "")


def describe_event(
    event: dict[str, Any], scopes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Shape one raw trace event for the timeline UI."""
    etype = event.get("type", "")
    p = event.get("payload", {})
    category = "other"
    title = etype
    detail = ""
    badges: list[str] = []

    if etype == "run_started":
        category, title = "run", "Run started"
        detail = p.get("task_text", "")
    elif etype == "run_finished":
        category, title = "run", f"Run finished: {p.get('status', '?')}"
        if p.get("status") == "done":
            detail = f"steps completed: {p.get('steps_completed', '?')}"
        else:
            detail = str(p.get("error", ""))
    elif etype == "plan_created":
        steps = p.get("steps", [])
        category, title = "plan", f"Plan created: {len(steps)} steps"
        detail = " -> ".join(str(s.get("tool", "?")) for s in steps)
    elif etype == "state_transition":
        category, title = "transition", f"State: {p.get('from')} -> {p.get('to')}"
    elif etype == "model_call":
        category, title = "model", f"Model call: {p.get('step_kind', '')}"
        detail = (
            f"model {p.get('model')} | {p.get('input_tokens')} in / "
            f"{p.get('output_tokens')} out tokens | "
            f"{p.get('latency_ms')} ms | cost "
            f"{_fmt_cost(p.get('cost_usd'), p.get('cost_is_estimate'))}"
        )
        if p.get("fallback_position"):
            badges.append(f"fallback #{p['fallback_position']}")
    elif etype == "model_call_failed":
        category, title = "model", f"Model call failed: {p.get('step_kind', '')}"
        detail = str(p.get("error", ""))
        badges.append("fallback taken" if p.get("fallback_position") is not None else "")
        badges = [b for b in badges if b]
    elif etype == "tool_call":
        tool = str(p.get("tool", "?"))
        info = scopes.get(tool, {})
        scope = info.get("scope", "unknown")
        category, title = "tool", f"Tool call: {tool}"
        badges.append(f"scope: {scope}")
        if info.get("needs_approval"):
            badges.append("approval gated")
        status = "ok" if p.get("ok") else "FAILED"
        detail = (
            f"step {p.get('step_index')} | {status} | {p.get('latency_ms')} ms | "
            f"args {json.dumps(p.get('args', {}), default=str)}"
        )
        if p.get("ok"):
            output = str(p.get("output", ""))
            detail += f" | output: {output[:160]}"
        else:
            detail += f" | error: {str(p.get('error', ''))[:200]}"
    elif etype == "step_verified":
        category, title = "verify", "Step verified"
        detail = f"step {p.get('step_index')} | tool {p.get('tool')}"
    elif etype == "recovery_event":
        kind = str(p.get("failure_kind", ""))
        if kind in GATED_KINDS:
            category, title = "gate", "Permission gate: action refused"
        else:
            category, title = "recovery", f"Recovery: {kind}"
        detail = (
            f"step {p.get('step_index')} | tool {p.get('tool')} | "
            f"action {p.get('action')} | strategy {p.get('strategy')} | "
            f"attempt {p.get('attempt')} | diagnosis: {p.get('diagnosis')}"
        )
    return {
        "seq": event.get("seq"),
        "ts": event.get("ts"),
        "time": _iso(event.get("ts", 0.0)),
        "type": etype,
        "category": category,
        "title": title,
        "detail": detail,
        "badges": badges,
        "is_recovery": category in ("recovery", "gate"),
        "is_gate": category == "gate",
    }


def load_run_detail(
    trace_path: str | Path,
    run_id: str,
    manifest: Optional[dict[str, Any]] = None,
    scopes: Optional[dict[str, dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    if manifest is None:
        manifest = load_manifest(trace_path)
    if scopes is None:
        scopes = tool_scopes()
    events = list(read_events(trace_path, run_id=run_id))
    if not events:
        return None
    events.sort(key=lambda e: (e.get("ts", 0), e.get("seq", 0)))
    return {
        "summary": summarize_run(run_id, events, manifest),
        "events": [describe_event(e, scopes) for e in events],
    }
