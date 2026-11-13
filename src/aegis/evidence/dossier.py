"""Building an evidence dossier from the live data clients.

A dossier is the product's working file for one question: a gene, and
optionally one variant in it. The builder asks every real source the
product has (ClinVar for classifications, PubMed for literature, and
gnomAD for population frequencies when a variant is named), turns each
returned record into one evidence item, and keeps a snapshot of which
records the clients actually returned in this run. That snapshot is
what the citation checker later uses to prove no item was invented:
an item whose record is not in the snapshot cannot have come from a
client in this run.

A source that fails does not sink the dossier and does not come back
as silence. The failure is written into ``source_errors`` in plain
words, so the report can say "gnomAD did not answer" instead of
letting a reader assume gnomAD had nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..data import DiskCache, lookup_variant, search_clinvar, search_pubmed
from ..data.errors import DataSourceError
from ..data.http import Transport

# Source names, used everywhere (items, counts, report headings).
SOURCE_CLINVAR = "clinvar"
SOURCE_PUBMED = "pubmed"
SOURCE_GNOMAD = "gnomad"


@dataclass
class EvidenceQuery:
    """One evidence question: a gene, and a variant when there is one.

    ``variant`` is the gnomAD style variant id (chrom-pos-ref-alt,
    for example ``17-43044295-G-A``) when the question is about one
    specific variant. Without it, gnomAD has nothing to look up and
    the dossier rests on ClinVar and PubMed alone.
    """

    gene: str
    variant: Optional[str] = None

    @property
    def text(self) -> str:
        """The question as a person would write it, for reports."""
        if self.variant:
            return f"{self.gene} variant {self.variant}"
        return self.gene


def parse_query(text: str) -> EvidenceQuery:
    """Read a query string: ``BRCA1`` or ``BRCA1 17-43044295-G-A``.

    The first word is the gene. A second word, when present, is the
    variant id. Anything beyond that is rejected instead of guessed
    at, because a query the builder misreads produces a dossier about
    the wrong thing, confidently labelled.
    """
    words = text.split()
    if not words:
        raise ValueError("an evidence query needs at least a gene name")
    if len(words) > 2:
        raise ValueError(
            "an evidence query is a gene and optionally one variant id, "
            f"got {len(words)} words: {text!r}"
        )
    return EvidenceQuery(gene=words[0], variant=words[1] if len(words) == 2 else None)


@dataclass
class EvidenceItem:
    """One piece of evidence, tied to the exact record it came from."""

    source: str  # one of the SOURCE_ constants above
    record_id: str  # PMID, ClinVar variation id, or gnomAD variant id
    title: str
    summary: str
    source_url: str  # the record's own page at the source
    fetched_at: str = ""  # when the client fetched the record live
    from_cache: bool = False  # True when this copy came from the cache

    @property
    def key(self) -> tuple[str, str]:
        """The (source, record id) pair the citation checker matches."""
        return (self.source, self.record_id)


@dataclass
class EvidenceDossier:
    """Everything gathered for one query, before any checking.

    ``returned_keys`` is the builder's snapshot of the (source, record
    id) pairs the clients returned in this run. It is recorded at
    build time, separately from the items themselves, so an item added
    later by hand (or invented by a model) is visible as exactly that:
    its key is not in the snapshot.
    """

    query: EvidenceQuery
    items: list[EvidenceItem] = field(default_factory=list)
    returned_keys: tuple[tuple[str, str], ...] = ()
    source_errors: list[str] = field(default_factory=list)

    def count_by_source(self) -> dict[str, int]:
        """How many items came from each source, in source order."""
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.source] = counts.get(item.source, 0) + 1
        return counts

    def sources(self) -> list[str]:
        """The distinct sources that contributed at least one item."""
        return sorted({item.source for item in self.items})


def _clinvar_item(record) -> EvidenceItem:
    """Turn one ClinVar record into an evidence item.

    The summary carries the fields a reviewer weighs first: the
    classification, how much review stands behind it, when it was
    last evaluated, and the condition named on the record. The
    versioned accession goes in the summary because the ClinVar URL
    is keyed by variation id, not by accession.
    """
    parts = [f"Classification: {record.classification or 'not stated'}."]
    if record.review_status:
        parts.append(f"Review status: {record.review_status}.")
    if record.last_evaluated:
        parts.append(f"Last evaluated: {record.last_evaluated}.")
    if record.trait:
        parts.append(f"Condition: {record.trait}.")
    if record.accession:
        parts.append(f"Accession: {record.accession}.")
    return EvidenceItem(
        source=SOURCE_CLINVAR,
        record_id=record.variation_id,
        title=record.title,
        summary=" ".join(parts),
        source_url=record.source_url,
        fetched_at=record.fetched_at,
        from_cache=record.from_cache,
    )


def _pubmed_item(record, abstract_limit: int = 600) -> EvidenceItem:
    """Turn one PubMed record into an evidence item.

    The summary is the journal and year plus the start of the
    abstract. Abstracts are long; the report needs enough to show
    what the paper is about, not the whole text, and the full record
    is one click away at the source URL.
    """
    where = record.journal
    if record.year:
        where = f"{where} ({record.year})" if where else record.year
    abstract = record.abstract.strip()
    if len(abstract) > abstract_limit:
        abstract = abstract[:abstract_limit].rstrip() + " ..."
    summary = ". ".join(part for part in [where, abstract] if part)
    return EvidenceItem(
        source=SOURCE_PUBMED,
        record_id=record.pmid,
        title=record.title,
        summary=summary,
        source_url=record.source_url,
        fetched_at=record.fetched_at,
        from_cache=record.from_cache,
    )


def _gnomad_item(record) -> EvidenceItem:
    """Turn one gnomAD record into an evidence item.

    The summary states the allele frequencies plainly, with the
    counts behind them, and says which dataset they came from,
    because frequencies differ between gnomAD releases.
    """

    def one_population(label: str, ac, an, af) -> Optional[str]:
        if ac is None or an is None or af is None:
            return None
        return f"{label}: allele frequency {af} (allele count {ac} of {an})"

    parts = [
        part
        for part in [
            one_population("Genome", record.genome_ac, record.genome_an, record.genome_af),
            one_population("Exome", record.exome_ac, record.exome_an, record.exome_af),
        ]
        if part
    ]
    if not parts:
        parts = ["gnomAD holds this variant but reports no genome or exome counts."]
    summary = f"Dataset {record.dataset}. " + ". ".join(parts) + "."
    return EvidenceItem(
        source=SOURCE_GNOMAD,
        record_id=record.variant_id,
        title=f"Population frequencies for variant {record.variant_id}",
        summary=summary,
        source_url=record.source_url,
        fetched_at=record.fetched_at,
        from_cache=record.from_cache,
    )


def build_evidence_dossier(
    query: EvidenceQuery | str,
    max_per_source: int = 5,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
) -> EvidenceDossier:
    """Gather evidence for one query from every live source.

    ``query`` is an EvidenceQuery, or a string that parse_query can
    read. Each source is asked in turn. A source that raises a
    DataSourceError is noted in ``source_errors`` and the build
    continues with what the other sources returned, because a partial
    dossier that says what is missing is useful, and one that hides a
    failed source is not. The gate, not the builder, decides whether
    what came back is enough to answer.
    """
    if isinstance(query, str):
        query = parse_query(query)
    dossier = EvidenceDossier(query=query)

    # ClinVar: classifications for variants in this gene. The query
    # uses ClinVar's own gene field syntax, as the client expects.
    try:
        records = search_clinvar(
            f"{query.gene}[gene]",
            max_results=max_per_source,
            cache=cache,
            transport=transport,
        )
        dossier.items.extend(_clinvar_item(record) for record in records)
    except DataSourceError as exc:
        dossier.source_errors.append(f"clinvar: {exc.kind}: {exc.detail}")

    # PubMed: the literature on this gene (and the variant, when the
    # query names one in words a literature search can match).
    pubmed_term = query.gene
    if query.variant and ("c." in query.variant or "p." in query.variant):
        pubmed_term = f"{query.gene} {query.variant}"
    try:
        records = search_pubmed(
            pubmed_term,
            max_results=max_per_source,
            cache=cache,
            transport=transport,
        )
        dossier.items.extend(_pubmed_item(record) for record in records)
    except DataSourceError as exc:
        dossier.source_errors.append(f"pubmed: {exc.kind}: {exc.detail}")

    # gnomAD: population frequencies, only when a variant is named.
    # A variant gnomAD does not hold is a normal finding in this work,
    # and it arrives as a typed not_found error, so it is recorded as
    # a source note, never as a frequency of zero.
    if query.variant:
        try:
            record = lookup_variant(
                query.variant, cache=cache, transport=transport
            )
            dossier.items.append(_gnomad_item(record))
        except DataSourceError as exc:
            dossier.source_errors.append(f"gnomad: {exc.kind}: {exc.detail}")

    # Snapshot what the clients actually returned, in the order the
    # items were built. The citation checker trusts this list, not
    # the items, which is the whole point of keeping it separate.
    dossier.returned_keys = tuple(item.key for item in dossier.items)
    return dossier
