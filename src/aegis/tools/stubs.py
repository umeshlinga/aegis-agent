"""Stub tools for local runs and tests.

These are real handlers over a small in-memory note store. They exist so the
full runtime loop (plan, act, verify, recover, trace) can be exercised
without any external service. Phase 2 adds the browser tool and API tools
next to these, under the same registry contract.
"""

from __future__ import annotations

from .registry import PermissionScope, RunPolicy, ToolSpec


class NoteStore:
    """Tiny in-memory key-to-text store standing in for an external system."""

    def __init__(self) -> None:
        self._notes: dict[str, str] = {"inbox": "first line"}

    def read(self, key: str) -> str:
        if key not in self._notes:
            raise KeyError(f"no note under key '{key}'")
        return self._notes[key]

    def append(self, key: str, line: str) -> str:
        self._notes[key] = self._notes.get(key, "") + ("\n" if key in self._notes else "") + line
        return self._notes[key]


def make_note_tools(store: NoteStore) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="read_note",
            description="Read the text stored under a key.",
            scope=PermissionScope.READ,
            handler=store.read,
            policy=RunPolicy(timeout_s=5.0),
        ),
        ToolSpec(
            name="append_note",
            description="Append one line of text to the note stored under a key.",
            scope=PermissionScope.WRITE,
            handler=store.append,
            policy=RunPolicy(timeout_s=5.0),
        ),
    ]


def make_charge_tool(ledger: list) -> ToolSpec:
    """A spend-scope, irreversible stub used to exercise the approval gate."""

    def charge(amount_usd: float, memo: str = "") -> dict:
        entry = {"amount_usd": amount_usd, "memo": memo}
        ledger.append(entry)
        return entry

    return ToolSpec(
        name="charge_card",
        description="Charge a payment method. Irreversible.",
        scope=PermissionScope.SPEND,
        handler=charge,
        irreversible=True,
    )
