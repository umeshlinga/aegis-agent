"""Loading and validating the comparison task set.

The task set is a JSON file (evals/comparison_tasks.json) of real
bioinformatics evidence questions. Each task names a gene, an
optional variant, the outcome the source records support
(``answerable`` or ``decline``), and ground truth notes naming the
records that decide it and the date they were checked. Loading is
strict on purpose: a task without ground truth notes, or with an
expected outcome outside the two the harness knows how to score, is
rejected at load time instead of being scored loosely later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EXPECTED_OUTCOMES = ("answerable", "decline")


@dataclass
class ComparisonTask:
    """One real evidence question and its source backed expectation."""

    id: str
    gene: str
    question: str
    task_type: str  # "answerable" or "decline", mirrors expected_outcome
    expected_outcome: str  # "answerable" or "decline"
    ground_truth: str  # notes naming the deciding source records
    checked: str  # date the ground truth was checked, ISO form
    variant: Optional[str] = None


def _task_from_raw(raw: dict, where: str) -> ComparisonTask:
    """Build one task from its JSON object, rejecting loose shapes."""
    for field_name in ("id", "gene", "question", "expected_outcome", "ground_truth"):
        if not raw.get(field_name):
            raise ValueError(f"{where}: task is missing a usable {field_name!r}")
    expected = raw["expected_outcome"]
    if expected not in EXPECTED_OUTCOMES:
        raise ValueError(
            f"{where}: expected_outcome must be one of {EXPECTED_OUTCOMES}, "
            f"got {expected!r}"
        )
    return ComparisonTask(
        id=raw["id"],
        gene=raw["gene"],
        question=raw["question"],
        task_type=raw.get("task_type") or expected,
        expected_outcome=expected,
        ground_truth=raw["ground_truth"],
        checked=raw.get("checked") or "",
        variant=raw.get("variant") or None,
    )


def load_tasks(path: Path | str) -> list[ComparisonTask]:
    """Read a task set file and return its tasks, in file order."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError(f"{path}: task set has no tasks list")
    tasks = [
        _task_from_raw(raw, f"{path} task {index + 1}")
        for index, raw in enumerate(raw_tasks)
    ]
    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            raise ValueError(f"{path}: duplicate task id {task.id!r}")
        seen.add(task.id)
    return tasks
