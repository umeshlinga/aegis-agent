"""Trace store tests: events are written as structured records and can be
queried back by run id and type, in order."""

from aegis.trace.store import TraceWriter, read_events


def test_write_and_query(tmp_path):
    path = tmp_path / "trace.jsonl"
    writer = TraceWriter(path)
    writer.emit("run-a", "run_started", task_id="t1")
    writer.emit("run-a", "tool_call", tool="read_note", ok=True)
    writer.emit("run-b", "run_started", task_id="t2")

    all_events = list(read_events(path))
    assert [e["seq"] for e in all_events] == [1, 2, 3]

    run_a = list(read_events(path, run_id="run-a"))
    assert [e["type"] for e in run_a] == ["run_started", "tool_call"]
    assert run_a[1]["payload"]["tool"] == "read_note"

    tool_calls = list(read_events(path, type="tool_call"))
    assert len(tool_calls) == 1

    assert list(read_events(tmp_path / "missing.jsonl")) == []
