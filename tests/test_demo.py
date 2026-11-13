"""Demo narration tests: the narrator turns trace events into the
walkthrough lines, keeps the recovery and gate beats, and compresses
routine transitions away. Synthetic events only; the full demo run is
exercised by `make demo`."""

from aegis.demo.run import BEATS, narrate_event
from aegis.explorer.data import tool_scopes


def _event(type_, **payload):
    return {"run_id": "r", "seq": 1, "ts": 0.0, "type": type_, "payload": payload}


def test_narration_covers_the_story_beats():
    scopes = tool_scopes()
    lines = [
        narrate_event(
            _event("plan_created", steps=[{"tool": "browser_click"}]), scopes
        ),
        narrate_event(
            _event(
                "tool_call", step_index=0, tool="browser_click", ok=False,
                error="no element", latency_ms=3.0,
            ),
            scopes,
        ),
        narrate_event(
            _event(
                "recovery_event", step_index=0, tool="browser_click",
                failure_kind="element_not_found", diagnosis="missing",
                strategy="re-ground", action="retry_changed_strategy",
                attempt=0,
            ),
            scopes,
        ),
        narrate_event(
            _event("run_finished", status="done", steps_completed=2), scopes
        ),
    ]
    assert all(lines)
    assert "scope act" in lines[1]
    assert "RECOVERY" in lines[2] and "re-ground" in lines[2]


def test_narration_marks_the_gate_as_final():
    line = narrate_event(
        _event(
            "recovery_event", step_index=0, tool="charge_card",
            failure_kind="permission_denied", diagnosis="no approval",
            strategy="none", action="abort", attempt=0,
        ),
        tool_scopes(),
    )
    assert line is not None and "PERMISSION GATE" in line


def test_narration_skips_routine_transitions():
    scopes = tool_scopes()
    assert narrate_event(
        _event("state_transition", **{"from": "plan", "to": "act"}), scopes
    ) is None
    assert narrate_event(
        _event("state_transition", **{"from": "act", "to": "recover"}), scopes
    ) is not None


def test_beats_reference_real_suite_tasks():
    from pathlib import Path

    from aegis.evals.suite import load_suite

    suite = load_suite(Path(__file__).parent.parent / "evals" / "tasks.json")
    ids = {t.id for t in suite.tasks}
    for beat in BEATS:
        for task_id in beat["tasks"]:
            assert task_id in ids
