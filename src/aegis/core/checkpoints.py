"""Checkpoint store.

After every verified step the runner saves a snapshot: which steps are
done, their outputs, and the run state at that point. Recovery can roll
back to the newest checkpoint instead of restarting the run, and a crashed
process can resume from the last verified step. Phase 1 stores one JSON
file per run on local disk; the interface is the contract, not the medium.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


class CheckpointStore:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"

    def save(self, run_id: str, step_index: int, snapshot: dict[str, Any]) -> None:
        record = {"run_id": run_id, "step_index": step_index, "snapshot": snapshot}
        self._path(run_id).write_text(json.dumps(record, indent=2), encoding="utf-8")

    def load_latest(self, run_id: str) -> Optional[dict[str, Any]]:
        path = self._path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
