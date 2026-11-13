"""Eval harness entry point.

Phase 1: loads and validates a suite and prints what would run.
Phase 2: executes each task twice per configuration (recovery on and
recovery off), scores pass or fail from the trace, and writes the results
table the README points at. No results are produced, or claimed, until
that execution code exists and has actually run.
"""

from __future__ import annotations

import argparse

from .suite import load_suite


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis-evals")
    parser.add_argument("--suite", required=True, help="path to a suite JSON file")
    args = parser.parse_args()

    suite = load_suite(args.suite)
    kinds: dict[str, int] = {}
    for task in suite.tasks:
        kinds[task.kind] = kinds.get(task.kind, 0) + 1
    print(f"suite: {suite.name}")
    print(f"tasks: {len(suite.tasks)} ({kinds})")
    for task in suite.tasks:
        print(f"  - {task.id} [{task.kind}] scorer={task.scorer.get('type')}")
    print("execution and scoring land in Phase 2; nothing was run.")


if __name__ == "__main__":
    main()
