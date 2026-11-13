"""Rendering a checked dossier as a Markdown report a person reads.

The report is the product surface of this layer: question, verdict,
the evidence that passed checking grouped by source with the URL of
each record, the citations that failed checking and why they were
removed, any source that did not answer, and a footer recording the
provenance of what is shown (which sources, when each record was
fetched, and whether it came from a live fetch or the cache).

A declined dossier renders as a complete report too. The decline is
the answer in that case, and it is written with the same care: what
was found, what it was not enough of, and what is missing.
"""

from __future__ import annotations

from .citations import CitationCheckResult
from .dossier import EvidenceDossier, EvidenceItem
from .gate import GateVerdict

# Display names and a stable reading order for the sources. Evidence
# reads best in the order a reviewer weighs it: classifications
# first, then literature, then population frequencies.
_SOURCE_LABELS = {
    "clinvar": "ClinVar",
    "pubmed": "PubMed",
    "gnomad": "gnomAD",
}
_SOURCE_ORDER = ["clinvar", "pubmed", "gnomad"]


def _label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)


def _provenance_line(item: EvidenceItem) -> str:
    """One item's fetch record: when, and live or from cache."""
    origin = "from cache" if item.from_cache else "live fetch"
    when = item.fetched_at or "fetch time not recorded"
    return f"Fetched {when} ({origin})."


def render_report(
    dossier: EvidenceDossier,
    check: CitationCheckResult,
    verdict: GateVerdict,
) -> str:
    """Render the full Markdown report for one checked dossier."""
    query = dossier.query
    lines: list[str] = []

    lines.append(f"# Evidence report: {query.text}")
    lines.append("")
    if query.variant:
        lines.append(
            f"Question: what is known about variant {query.variant} "
            f"in {query.gene}, and how strong is the evidence?"
        )
    else:
        lines.append(
            f"Question: what is known about {query.gene}, and how "
            "strong is the evidence?"
        )
    lines.append("")

    lines.append("## Verdict")
    lines.append("")
    if verdict.answerable:
        lines.append(f"**Answerable.** {verdict.reason}")
    else:
        lines.append(f"**Declined.** {verdict.reason}")
        lines.append("")
        lines.append(
            "No conclusion is drawn from evidence this thin. The "
            "items below are what was found and verified; they are "
            "shown so the gap is visible, not filled in."
        )
    lines.append("")

    lines.append("## Evidence")
    lines.append("")
    if not check.verified:
        lines.append("No evidence items passed citation checking.")
        lines.append("")
    for source in _SOURCE_ORDER:
        items = [item for item in check.verified if item.source == source]
        if not items:
            continue
        lines.append(f"### {_label(source)} ({len(items)})")
        lines.append("")
        for item in items:
            lines.append(f"- **{item.title}**")
            lines.append(f"  Record: {item.record_id}")
            lines.append(f"  {item.summary}")
            lines.append(f"  Source: {item.source_url}")
            lines.append(f"  {_provenance_line(item)}")
        lines.append("")

    if check.failures:
        lines.append("## Citations removed by the checker")
        lines.append("")
        lines.append(
            "These items were in the gathered dossier and did not "
            "pass citation checking. They support nothing in this "
            "report."
        )
        lines.append("")
        for failure in check.failures:
            lines.append(
                f"- {_label(failure.source)} record {failure.record_id} "
                f"({failure.title}): {failure.reason}"
            )
        lines.append("")

    if dossier.source_errors:
        lines.append("## Sources that did not answer")
        lines.append("")
        for note in dossier.source_errors:
            lines.append(f"- {note}")
        lines.append("")

    lines.append("## Sources and provenance")
    lines.append("")
    if check.verified:
        by_source: dict[str, list[EvidenceItem]] = {}
        for item in check.verified:
            by_source.setdefault(item.source, []).append(item)
        for source in _SOURCE_ORDER:
            items = by_source.get(source)
            if not items:
                continue
            fetch_times = sorted({item.fetched_at for item in items if item.fetched_at})
            cached = sum(1 for item in items if item.from_cache)
            times_text = ", ".join(fetch_times) if fetch_times else "not recorded"
            lines.append(
                f"- {_label(source)}: {len(items)} verified item"
                + ("s" if len(items) != 1 else "")
                + f", fetched at {times_text}, "
                + f"{cached} from cache, {len(items) - cached} from live fetch."
            )
    else:
        lines.append("- No verified evidence, so no provenance to record.")
    lines.append(
        f"- Gathered before checking: {len(dossier.items)} item"
        + ("s" if len(dossier.items) != 1 else "")
        + f" from {len(dossier.sources())} source"
        + ("s" if len(dossier.sources()) != 1 else "")
        + f"; removed by the citation checker: {len(check.failures)}."
    )
    lines.append("")
    return "\n".join(lines)
