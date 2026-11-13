"""Eval harness entry point.

Executes the suite: each task runs against the full runtime twice,
recovery enabled and recovery disabled, and is scored from final state
plus trace assertions (see aegis.evals.execute). The results table is
printed and also written to a dated file under the results directory.
No number is produced by anything other than an actual run.
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from .execute import render_results, run_suite
from .suite import load_suite


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis-evals")
    parser.add_argument("--suite", required=True, help="path to a suite JSON file")
    parser.add_argument(
        "--results-dir",
        default="evals/results",
        help="directory for the dated results file",
    )
    parser.add_argument(
        "--workdir",
        default="traces/evals",
        help="directory for traces and checkpoints from the runs",
    )
    args = parser.parse_args()

    suite = load_suite(args.suite)
    date_str = datetime.date.today().isoformat()
    outcomes = run_suite(suite, args.workdir)
    report = render_results(suite, outcomes, date_str)

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"results_{suite.name}_{date_str}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"results written to {out_path}")


if __name__ == "__main__":
    main()
