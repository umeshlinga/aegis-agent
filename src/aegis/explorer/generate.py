"""Generate the trace explorer's demo data set.

Runs a small, deliberate selection of eval tasks through the real eval
runner (both recovery modes) into a dedicated trace directory, and
writes a runs.json manifest next to the trace file so the explorer can
label each run with its task and mode. The trace file itself stays the
runtime's own format; the manifest is explorer metadata only.

Selection intent: at least one designed-failure browser task
(b02-renamed-button), at least one refused spend task
(a04-charge-refused-no-approval), plus an approved spend, a refused
irreversible browser action, a transient form recovery, a flaky API
recovery, and two clean runs for contrast.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..evals.execute import run_task
from ..evals.suite import load_suite
from ..testsite.server import make_test_site
from ..trace.store import read_events

DEMO_TASKS = [
    "b02-renamed-button",
    "b03-form-transient-rejection",
    "b11-clean-form",
    "b07-delete-refused-no-approval",
    "a04-charge-refused-no-approval",
    "a05-charge-approved",
    "a07-flaky-append-once",
    "a01-read-inbox",
]


def _known_run_ids(trace_path: Path) -> set[str]:
    return {e["run_id"] for e in read_events(trace_path)}


def generate(
    trace_dir: str | Path,
    suite_path: str | Path,
    task_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run the selected tasks into trace_dir and write the manifest.

    Returns one record per run: task id, mode, run id, scorer outcome.
    Any previous contents of trace_dir are replaced, so the data set is
    reproducible from this command alone.
    """
    trace_dir = Path(trace_dir)
    if trace_dir.exists():
        shutil.rmtree(trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / "traces.jsonl"

    suite = load_suite(suite_path)
    wanted = task_ids or DEMO_TASKS
    tasks = {t.id: t for t in suite.tasks}
    missing = [tid for tid in wanted if tid not in tasks]
    if missing:
        raise ValueError(f"tasks not in suite {suite.name}: {missing}")

    manifest: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    site = make_test_site()
    try:
        for task_id in wanted:
            task = tasks[task_id]
            for enabled in (True, False):
                before = _known_run_ids(trace_path)
                outcome = run_task(task, site, trace_dir, enabled)
                new_ids = [
                    rid for rid in _known_run_ids(trace_path) if rid not in before
                ]
                if len(new_ids) != 1:
                    raise RuntimeError(
                        f"expected exactly one new run for {task_id}, "
                        f"found {len(new_ids)}"
                    )
                run_id = new_ids[0]
                mode = "on" if enabled else "off"
                manifest[run_id] = {"task_id": task_id, "mode": mode}
                records.append(
                    {
                        "task_id": task_id,
                        "mode": mode,
                        "run_id": run_id,
                        "passed": outcome.passed,
                    }
                )
    finally:
        site.shutdown()

    (trace_dir / "runs.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return records
