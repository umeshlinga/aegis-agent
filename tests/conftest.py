import json

import pytest

from aegis.core.checkpoints import CheckpointStore
from aegis.core.state_machine import AgentRunner
from aegis.models.fake import ScriptedFakeModel
from aegis.models.router import ModelRouter
from aegis.recovery.engine import RecoveryEngine
from aegis.tools.registry import ToolRegistry
from aegis.tools.stubs import NoteStore, make_note_tools
from aegis.trace.store import TraceWriter


@pytest.fixture()
def make_runner(tmp_path):
    """Factory: build a runner whose planner is the scripted fake model,
    returning the given plan steps verbatim. Fully local, no keys."""

    def _make(plan_steps, extra_tools=(), recovery=None, approval=None, store=None):
        store = store if store is not None else NoteStore()
        registry = ToolRegistry()
        for spec in make_note_tools(store):
            registry.register(spec)
        for spec in extra_tools:
            registry.register(spec)
        fake = ScriptedFakeModel({"plan": json.dumps({"steps": plan_steps})})
        trace = TraceWriter(tmp_path / "trace.jsonl")
        router = ModelRouter({"fake": fake}, {"plan": ["fake"]}, trace=trace)
        runner = AgentRunner(
            registry=registry,
            router=router,
            trace=trace,
            checkpoints=CheckpointStore(tmp_path / "ckpts"),
            recovery=recovery if recovery is not None else RecoveryEngine(),
            approval=approval,
        )
        return runner, trace, store

    return _make
