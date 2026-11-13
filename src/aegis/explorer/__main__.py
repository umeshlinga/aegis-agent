"""Run the trace explorer: python -m aegis.explorer [--port 8765]
[--trace-dir traces/explorer] [--generate]

By default the explorer serves the demo trace set under
traces/explorer/. If that set does not exist yet, it is generated first
(real eval-runner runs, both modes, a few seconds). --generate forces a
fresh set. --trace-file serves any other trace file instead (modes are
then "not recorded" unless a runs.json manifest sits next to it).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .generate import generate
from .server import serve

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(prog="aegis.explorer")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--trace-dir",
        default=str(REPO_ROOT / "traces" / "explorer"),
        help="directory holding traces.jsonl (and runs.json if present)",
    )
    parser.add_argument("--trace-file", default=None)
    parser.add_argument(
        "--generate",
        action="store_true",
        help="regenerate the demo trace set before serving",
    )
    args = parser.parse_args()

    if args.trace_file:
        trace_path = Path(args.trace_file)
    else:
        trace_dir = Path(args.trace_dir)
        trace_path = trace_dir / "traces.jsonl"
        if args.generate or not trace_path.exists():
            print("Generating demo trace set (real runs, both modes)...")
            records = generate(trace_dir, REPO_ROOT / "evals" / "tasks.json")
            for record in records:
                print(
                    f"  {record['task_id']} | recovery {record['mode']} | "
                    f"run {record['run_id']} | scorer "
                    f"{'pass' if record['passed'] else 'FAIL'}"
                )

    server = serve(trace_path, port=args.port)
    print(f"Trace explorer: http://127.0.0.1:{args.port}/  (trace: {trace_path})")
    print("Read-only. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
