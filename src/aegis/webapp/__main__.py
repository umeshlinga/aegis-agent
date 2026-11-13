"""Run the Aegis product web app: python -m aegis.webapp [--port 8770]

Opens a local web product over the evidence core. Gathered source
responses are cached on disk (see --cache-dir), the same cache the
evidence command line uses, so asking the same question twice is
fast and the report page can say which records came from the cache
and when they were first fetched live.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..data import DiskCache
from .server import serve

DEFAULT_CACHE_DIR = Path(".aegis-cache")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aegis.webapp",
        description="Run the Aegis evidence product locally.",
    )
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"response cache directory (default {DEFAULT_CACHE_DIR})",
    )
    args = parser.parse_args(argv)

    server = serve(port=args.port, cache=DiskCache(args.cache_dir))
    print(f"Aegis product: http://127.0.0.1:{args.port}/", flush=True)
    print("Local product prototype, public data only. Ctrl-C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
