"""Permission gate tests: an irreversible spend tool cannot run without a
human approval decision, and runs when approval is granted."""

import pytest

from aegis.tools.registry import PermissionDenied, PermissionScope, ToolRegistry
from aegis.tools.stubs import make_charge_tool, make_note_tools, NoteStore


def _registry_with_charge(ledger):
    registry = ToolRegistry()
    registry.register(make_charge_tool(ledger))
    return registry


def test_spend_tool_blocked_without_approval_function():
    ledger = []
    registry = _registry_with_charge(ledger)
    with pytest.raises(PermissionDenied):
        registry.execute("charge_card", {"amount_usd": 10.0})
    assert ledger == []


def test_spend_tool_blocked_when_approval_declines():
    ledger = []
    registry = _registry_with_charge(ledger)
    with pytest.raises(PermissionDenied):
        registry.execute(
            "charge_card", {"amount_usd": 10.0}, approval=lambda spec, args: False
        )
    assert ledger == []


def test_spend_tool_runs_when_approval_granted():
    ledger = []
    registry = _registry_with_charge(ledger)
    seen = {}

    def approve(spec, args):
        seen["scope"] = spec.scope
        return True

    result = registry.execute("charge_card", {"amount_usd": 10.0}, approval=approve)
    assert result.ok
    assert ledger == [{"amount_usd": 10.0, "memo": ""}]
    assert seen["scope"] == PermissionScope.SPEND


def test_read_tool_needs_no_approval():
    registry = ToolRegistry()
    for spec in make_note_tools(NoteStore()):
        registry.register(spec)
    result = registry.execute("read_note", {"key": "inbox"})
    assert result.ok
    assert result.output == "first line"
    declared = {d["name"]: d for d in registry.describe()}
    assert declared["read_note"]["needs_approval"] is False
    assert declared["append_note"]["scope"] == "write"
