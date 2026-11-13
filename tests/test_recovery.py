"""Recovery tests: a transient tool failure is classified, a recovery event
is traced with its diagnosis and strategy, and the retry completes the run.
With recovery disabled (the eval ablation), the same failure aborts."""

from aegis.core.state_machine import TaskSpec
from aegis.recovery.engine import RecoveryEngine
from aegis.tools.registry import PermissionScope, RunPolicy, ToolSpec
from aegis.trace.store import read_events


def _flaky_tool():
    calls = {"n": 0}

    def flaky_write(line: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient downstream error")
        return f"wrote: {line}"

    return ToolSpec(
        name="flaky_write",
        description="Fails on its first call, succeeds after.",
        scope=PermissionScope.WRITE,
        handler=flaky_write,
        policy=RunPolicy(timeout_s=5.0),
    )


PLAN = [{"tool": "flaky_write", "args": {"line": "recovered line"}}]


def test_transient_failure_recovers(make_runner):
    runner, trace, _ = make_runner(PLAN, extra_tools=[_flaky_tool()])
    result = runner.run(TaskSpec(id="recovery-001", text="write once, flaky"))

    assert result.status == "done"
    assert result.outputs[0] == "wrote: recovered line"

    events = list(read_events(trace.path, run_id=result.run_id, type="recovery_event"))
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["failure_kind"] == "tool_error"
    assert payload["action"] == "retry_changed_strategy"
    assert payload["strategy"] == "retry_with_backoff_and_reduced_payload"
    assert "diagnosis" in payload and payload["diagnosis"]


def test_recovery_disabled_aborts(make_runner):
    runner, trace, _ = make_runner(
        PLAN,
        extra_tools=[_flaky_tool()],
        recovery=RecoveryEngine(enabled=False),
    )
    result = runner.run(TaskSpec(id="recovery-002", text="write once, flaky"))

    assert result.status == "failed"
    events = list(read_events(trace.path, run_id=result.run_id, type="recovery_event"))
    assert len(events) == 1
    assert events[0]["payload"]["action"] == "abort"
    assert events[0]["payload"]["strategy"] == "recovery_disabled"
