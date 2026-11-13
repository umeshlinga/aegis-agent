"""Trace store: every step of a run recorded as structured events.

One JSON object per line in a .jsonl file. The trace explorer (Phase 3) and
the eval harness read from these files; nothing in the runtime keeps run
history only in memory.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class TraceEvent:
    run_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "payload": self.payload,
        }


class TraceWriter:
    """Appends events to a JSONL trace file with a per-writer sequence number."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def emit(self, run_id: str, type: str, **payload: Any) -> TraceEvent:
        self._seq += 1
        event = TraceEvent(run_id=run_id, type=type, payload=payload, seq=self._seq)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")
        return event


def read_events(
    path: str | Path, run_id: Optional[str] = None, type: Optional[str] = None
) -> Iterator[dict[str, Any]]:
    """Read events back, optionally filtered by run id and/or event type.

    This is the query surface the trace explorer builds on: filter by run,
    then walk the ordered events to reconstruct exactly what the agent did.
    """
    p = Path(path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if run_id is not None and event.get("run_id") != run_id:
                continue
            if type is not None and event.get("type") != type:
                continue
            yield event
