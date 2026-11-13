"""Trace explorer tests: run aggregation, the manifest's mode labels,
event shaping (scope badges, recovery and gate highlighting), and the
read-only HTTP surface. All on synthetic trace files; no browser."""

import json

from aegis.explorer import data
from aegis.explorer.server import serve


def _write_trace(tmp_path):
    path = tmp_path / "traces.jsonl"
    events = [
        {"run_id": "run-1", "seq": 1, "ts": 1000.0, "type": "run_started",
         "payload": {"task_id": "t-charge", "task_text": "Charge the card."}},
        {"run_id": "run-1", "seq": 2, "ts": 1000.1, "type": "model_call",
         "payload": {"step_kind": "plan", "model": "fake-scripted",
                     "input_tokens": 10, "output_tokens": 5,
                     "latency_ms": 1.5, "cost_usd": 0.0,
                     "cost_is_estimate": False}},
        {"run_id": "run-1", "seq": 3, "ts": 1000.2, "type": "recovery_event",
         "payload": {"step_index": 0, "tool": "charge_card",
                     "failure_kind": "permission_denied",
                     "diagnosis": "gate refused", "action": "abort",
                     "strategy": "none", "attempt": 0}},
        {"run_id": "run-1", "seq": 4, "ts": 1001.0, "type": "run_finished",
         "payload": {"status": "failed", "error": "gate refused"}},
        {"run_id": "run-2", "seq": 1, "ts": 2000.0, "type": "run_started",
         "payload": {"task_id": "t-click", "task_text": "Click through."}},
        {"run_id": "run-2", "seq": 2, "ts": 2000.3, "type": "tool_call",
         "payload": {"step_index": 0, "tool": "browser_click", "args": {},
                     "ok": False, "output": None, "error": "gone",
                     "latency_ms": 12.0}},
        {"run_id": "run-2", "seq": 3, "ts": 2000.4, "type": "recovery_event",
         "payload": {"step_index": 0, "tool": "browser_click",
                     "failure_kind": "element_not_found",
                     "diagnosis": "missing", "action": "retry_changed_strategy",
                     "strategy": "re-ground", "attempt": 0}},
        {"run_id": "run-2", "seq": 4, "ts": 2001.5, "type": "run_finished",
         "payload": {"status": "done", "steps_completed": 1}},
    ]
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    manifest = {
        "run-1": {"task_id": "t-charge", "mode": "on"},
        "run-2": {"task_id": "t-click", "mode": "off"},
    }
    (tmp_path / "runs.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_list_runs_uses_manifest_and_trace(tmp_path):
    runs = data.list_runs(_write_trace(tmp_path))
    by_id = {r["run_id"]: r for r in runs}
    assert by_id["run-1"]["task_id"] == "t-charge"
    assert by_id["run-1"]["mode"] == "on"
    assert by_id["run-1"]["outcome"] == "failed"
    assert by_id["run-1"]["duration_s"] == 1.0
    assert by_id["run-1"]["gate_events"] == 1
    assert by_id["run-2"]["outcome"] == "done"
    assert by_id["run-2"]["recovery_events"] == 1
    assert by_id["run-2"]["gate_events"] == 0


def test_mode_not_recorded_without_manifest(tmp_path):
    path = _write_trace(tmp_path)
    (tmp_path / "runs.json").unlink()
    runs = data.list_runs(path)
    assert all(r["mode"] == "not recorded" for r in runs)


def test_run_detail_shapes_events(tmp_path):
    detail = data.load_run_detail(_write_trace(tmp_path), "run-2")
    assert detail is not None
    categories = [e["category"] for e in detail["events"]]
    assert "recovery" in categories
    tool_events = [e for e in detail["events"] if e["category"] == "tool"]
    assert any("scope: act" in b for b in tool_events[0]["badges"])
    recovery = [e for e in detail["events"] if e["is_recovery"]]
    assert recovery and not recovery[0]["is_gate"]

    gate_detail = data.load_run_detail(_write_trace(tmp_path), "run-1")
    gates = [e for e in gate_detail["events"] if e["is_gate"]]
    assert len(gates) == 1
    assert data.load_run_detail(_write_trace(tmp_path), "nope") is None


def test_tool_scopes_come_from_factories():
    scopes = data.tool_scopes()
    assert scopes["charge_card"]["scope"] == "spend"
    assert scopes["charge_card"]["irreversible"] is True
    assert scopes["browser_confirm_irreversible"]["needs_approval"] is True
    assert scopes["read_note"]["scope"] == "read"


def test_server_is_read_only(tmp_path):
    import urllib.request
    import urllib.error

    server = serve(_write_trace(tmp_path), port=0)
    port = server.server_address[1]
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs") as resp:
            payload = json.loads(resp.read())
        assert {r["run_id"] for r in payload["runs"]} == {"run-1", "run-2"}

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/runs", data=b"{}", method="POST"
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("POST should be refused")
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
    finally:
        server.shutdown()
