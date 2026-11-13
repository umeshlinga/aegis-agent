"""Eval task suite loading and validation.

A suite is a JSON file: a name plus a list of tasks. Each task has an id,
a kind ("browser" or "api"), the instruction text given to the agent, and
a scorer declaration the harness uses to judge pass or fail from the run
trace and the final state. Keeping tasks as data, not code, means the
suite can grow without touching the harness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_KINDS = {"browser", "api"}


@dataclass
class EvalTask:
    id: str
    kind: str
    text: str
    scorer: dict[str, Any]
    setup: dict[str, Any] | None = None


@dataclass
class EvalSuite:
    name: str
    tasks: list[EvalTask]


def load_suite(path: str | Path) -> EvalSuite:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "suite" not in data or "tasks" not in data:
        raise ValueError("suite file needs 'suite' and 'tasks' keys")
    tasks: list[EvalTask] = []
    seen: set[str] = set()
    for raw in data["tasks"]:
        for key in ("id", "kind", "text", "scorer"):
            if key not in raw:
                raise ValueError(f"task missing '{key}': {raw!r}")
        if raw["kind"] not in VALID_KINDS:
            raise ValueError(f"task {raw['id']}: kind must be one of {VALID_KINDS}")
        if raw["id"] in seen:
            raise ValueError(f"duplicate task id: {raw['id']}")
        seen.add(raw["id"])
        tasks.append(
            EvalTask(
                id=raw["id"],
                kind=raw["kind"],
                text=raw["text"],
                scorer=raw["scorer"],
                setup=raw.get("setup"),
            )
        )
    return EvalSuite(name=data["suite"], tasks=tasks)
