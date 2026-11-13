"""End-to-end smoke test: a trivial two-step task runs through plan, act,
verify, checkpoint, and trace, with the scripted fake model and stub tools.
No external service, no API key."""

from aegis.core.checkpoints import CheckpointStore
from aegis.core.state_machine import TaskSpec
from aegis.trace.store import read_events

PLAN = [
    {"tool": "read_note", "args": {"key": "inbox"}},
    {
        "tool": "append_note",
        "args": {"key": "inbox", "line": "second line"},
        "expect_substring": "second line",
    },
]


def test_two_step_task_end_to_end(make_runner, tmp_path):
    runner, trace, store = make_runner(PLAN)
    result = runner.run(TaskSpec(id="smoke-001", text="read then append"))

    assert result.status == "done"
    assert result.steps_completed == 2
    assert store.read("inbox") == "first line\nsecond line"

    events = list(read_events(trace.path, run_id=result.run_id))
    types = [e["type"] for e in events]
    assert types[0] == "run_started"
    assert "plan_created" in types
    assert "model_call" in types
    assert types.count("tool_call") == 2
    assert types.count("step_verified") == 2
    assert types[-1] == "run_finished"
    assert events[-1]["payload"]["status"] == "done"

    # A checkpoint exists at the last verified step.
    checkpoint = CheckpointStore(tmp_path / "ckpts").load_latest(result.run_id)
    assert checkpoint is not None
    assert checkpoint["step_index"] == 1
