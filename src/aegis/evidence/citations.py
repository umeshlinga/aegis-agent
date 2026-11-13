"""The citation checker: the anti-hallucination gate.

Every evidence item that reaches a final answer passes through here
first. For each item the checker asks three questions:

1. Is the source URL well formed, and does it sit on the host that
   source actually uses?
2. Does the URL name the record the item claims? The PubMed URL must
   carry the item's PMID, the ClinVar URL its variation id, the
   gnomAD URL its variant id. A real URL pointing at a different
   record is a wrong citation, and it fails here.
3. Was this record actually returned by its client in this run? The
   item's (source, record id) pair must be in the dossier's build
   time snapshot of returned records. An item that was written from
   memory, by a person or by a model, is not in that snapshot, and
   it fails here no matter how plausible its URL looks.

An item that fails any check is removed from the verified list and
recorded as a CitationFailure with the reason in plain words. Nothing
is quietly edited into passing: the URL is never rewritten, the
record id is never corrected, because a checker that fixes citations
would hide exactly the failures it exists to catch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from .dossier import (
    SOURCE_CLINVAR,
    SOURCE_GNOMAD,
    SOURCE_PUBMED,
    EvidenceDossier,
    EvidenceItem,
)

# The host each source serves its record pages from. A citation URL
# on any other host is not a citation to that source.
_EXPECTED_HOSTS = {
    SOURCE_PUBMED: "pubmed.ncbi.nlm.nih.gov",
    SOURCE_CLINVAR: "www.ncbi.nlm.nih.gov",
    SOURCE_GNOMAD: "gnomad.broadinstitute.org",
}


@dataclass
class CitationFailure:
    """One item that did not pass, and why, in plain words."""

    source: str
    record_id: str
    title: str
    reason: str


@dataclass
class CitationCheckResult:
    """The outcome of checking one dossier."""

    verified: list[EvidenceItem] = field(default_factory=list)
    failures: list[CitationFailure] = field(default_factory=list)


def _url_problem(item: EvidenceItem) -> str | None:
    """Check the URL's shape and that it names the claimed record.

    Returns a plain words reason when there is a problem, or None
    when the URL is well formed and matches the record id.
    """
    parsed = urlparse(item.source_url or "")
    if parsed.scheme != "https" or not parsed.netloc:
        return f"source URL is not a well formed https URL: {item.source_url!r}"

    expected_host = _EXPECTED_HOSTS.get(item.source)
    if expected_host is None:
        return f"unknown evidence source: {item.source!r}"
    if parsed.netloc != expected_host:
        return (
            f"source URL is on host {parsed.netloc!r}, "
            f"but {item.source} records live on {expected_host!r}"
        )

    if item.source == SOURCE_PUBMED:
        # PubMed record pages are /<pmid>/ and the PMID is digits.
        if not item.record_id.isdigit():
            return f"PubMed record id is not a numeric PMID: {item.record_id!r}"
        if parsed.path != f"/{item.record_id}/":
            return (
                f"PubMed URL path {parsed.path!r} does not name "
                f"PMID {item.record_id}"
            )
    elif item.source == SOURCE_CLINVAR:
        # ClinVar variation pages are /clinvar/variation/<id>/ and
        # the record id the URL carries is the variation id.
        if not item.record_id.isdigit():
            return (
                "ClinVar record id is not a numeric variation id: "
                f"{item.record_id!r}"
            )
        if parsed.path != f"/clinvar/variation/{item.record_id}/":
            return (
                f"ClinVar URL path {parsed.path!r} does not name "
                f"variation {item.record_id}"
            )
    elif item.source == SOURCE_GNOMAD:
        # gnomAD variant pages are /variant/<chrom-pos-ref-alt> with
        # the dataset named in the query string.
        if not re.fullmatch(r"[0-9XY]+-\d+-[A-Za-z]+-[A-Za-z]+", item.record_id):
            return (
                "gnomAD record id is not a variant id of the form "
                f"chrom-pos-ref-alt: {item.record_id!r}"
            )
        if parsed.path != f"/variant/{item.record_id}":
            return (
                f"gnomAD URL path {parsed.path!r} does not name "
                f"variant {item.record_id}"
            )
        if "dataset" not in parse_qs(parsed.query):
            return "gnomAD URL does not name its dataset in the query string"
    return None


def check_citations(dossier: EvidenceDossier) -> CitationCheckResult:
    """Verify every item in a dossier, split into passed and failed.

    The order of the verified list matches the dossier's order, so a
    report built from it reads the same way the dossier was gathered.
    """
    returned = set(dossier.returned_keys)
    result = CitationCheckResult()
    for item in dossier.items:
        problem = _url_problem(item)
        if problem is None and item.key not in returned:
            # The URL looks right, but no client returned this record
            # in this run. That is the fabricated citation shape: a
            # plausible pointer to a record nobody fetched.
            problem = (
                f"record {item.record_id} from {item.source} was not "
                "returned by its client in this run"
            )
        if problem is None:
            result.verified.append(item)
        else:
            result.failures.append(
                CitationFailure(
                    source=item.source,
                    record_id=item.record_id,
                    title=item.title,
                    reason=problem,
                )
            )
    return result
