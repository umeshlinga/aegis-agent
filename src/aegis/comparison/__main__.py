"""Run the comparison harness from the command line.

Example:

    python -m aegis.comparison
    python -m aegis.comparison --tasks evals/comparison_tasks.json

The run executes the Aegis evidence configuration over the real
task set on live data (with the data layer cache, and the results
file records live versus cache per task), and writes the partial
results file under evals/results/. The two LLM configurations are
reported in that file as blocked on a live model API key. Nothing
is claimed beyond what ran.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from ..data import DiskCache
from .runner import run_comparison, write_results
from .tasks import load_tasks

DEFAULT_TASKS = Path("evals/comparison_tasks.json")
DEFAULT_CACHE_DIR = Path(".aegis-cache")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aegis.comparison",
        description="Run the comparison harness (Aegis configuration) on real tasks.",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=DEFAULT_TASKS,
        help=f"task set file (default {DEFAULT_TASKS})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"response cache directory (default {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="results file to write (default: evals/results/results_comparison_aegis_only_<date>.md)",
    )
    args = parser.parse_args(argv)

    raw = json.loads(args.tasks.read_text(encoding="utf-8"))
    suite = raw.get("suite") or "aegis-comparison-v1"
    tasks = load_tasks(args.tasks)
    run_date = date.today().isoformat()
    cache = DiskCache(args.cache_dir)
    run = run_comparison(tasks, run_date=run_date, cache=cache, suite=suite)

    out = args.out
    if out is None:
        out = Path(f"evals/results/results_comparison_aegis_only_{run_date}.md")
    write_results(run, out)

    print(f"Results written to {out}")
    for result in run.results:
        config = result.configuration
        if result.blocked:
            print(f"{config.name}: {config.blocked_reason}")
        elif result.aggregate is not None:
            agg = result.aggregate
            print(
                f"{config.name}: task success {agg.tasks_passed} of "
                f"{agg.tasks_run}, citations {agg.citations_verified} of "
                f"{agg.citations_total} verified, declines "
                f"{agg.declines_correct} of {agg.decline_tasks} correct, "
                f"mean latency {agg.mean_latency_seconds:.2f} s"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
