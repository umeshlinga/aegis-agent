"""Run the evidence core from the command line.

Example:

    python -m aegis.evidence BRCA1
    python -m aegis.evidence BRCA1 --variant 17-43044295-G-A

The run gathers a dossier from the live sources, checks every
citation, applies the decline gate, and writes the Markdown report
to a file, printing where the file went and the verdict in one line.
Gathered responses are cached on disk (see --cache-dir), so running
the same question twice is fast, and the report says which records
came from the cache and when they were first fetched live.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from ..data import DiskCache
from .citations import check_citations
from .dossier import EvidenceQuery, build_evidence_dossier
from .gate import evaluate_gate
from .report import render_report

DEFAULT_CACHE_DIR = Path(".aegis-cache")


def _slug(text: str) -> str:
    """Make a file name piece out of a gene or variant id."""
    return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-").lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aegis.evidence",
        description="Build a checked evidence report for a gene, from live sources.",
    )
    parser.add_argument("gene", help="gene symbol, for example BRCA1")
    parser.add_argument(
        "--variant",
        default=None,
        help="gnomAD variant id (chrom-pos-ref-alt), for example 17-43044295-G-A",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="max records per source (default 5)",
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
        help="report file to write (default: evidence_<gene>_<date>.md here)",
    )
    args = parser.parse_args(argv)

    query = EvidenceQuery(gene=args.gene, variant=args.variant)
    cache = DiskCache(args.cache_dir)
    dossier = build_evidence_dossier(query, max_per_source=args.limit, cache=cache)
    check = check_citations(dossier)
    verdict = evaluate_gate(check.verified)
    report = render_report(dossier, check, verdict)

    out = args.out
    if out is None:
        name = f"evidence_{_slug(args.gene)}_{date.today().isoformat()}.md"
        out = Path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    cached = sum(1 for item in dossier.items if item.from_cache)
    print(f"Report written to {out}")
    print(
        f"Gathered {len(dossier.items)} items "
        f"({cached} from cache), verified {len(check.verified)}, "
        f"removed {len(check.failures)}."
    )
    print(verdict.reason)
    if dossier.source_errors:
        print("Sources that did not answer:", file=sys.stderr)
        for note in dossier.source_errors:
            print(f"  {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
