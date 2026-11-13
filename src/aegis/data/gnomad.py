"""gnomAD client, on the gnomAD GraphQL API.

gnomAD holds allele frequencies from large population sequencing
cohorts. For variant work its job in this product is narrow and
important: how common is this variant in the general population? A
variant too common to cause a rare disease is strong evidence, and the
number has to come from the source, with the dataset named, because
frequencies differ between gnomAD releases.

One GraphQL query looks a variant up by its id (chrom-pos-ref-alt, on
GRCh38 for current datasets) and returns the genome and exome allele
counts, allele numbers, and allele frequencies. A variant gnomAD does
not hold is a normal outcome in this work, and the API answers it with
a "Variant not found" error and a null variant; this client turns that
into a typed not_found error so callers handle it deliberately,
instead of mistaking it for a frequency of zero.

API reference: https://gnomad.broadinstitute.org/api
No API key is required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .cache import DiskCache
from .errors import DataSourceError
from .http import Transport, fetch_text

SOURCE = "gnomad"
API_URL = "https://gnomad.broadinstitute.org/api"
DEFAULT_DATASET = "gnomad_r4"

# The one query this client sends. Population level breakdowns exist in
# the API and can be added here when a caller actually needs them; the
# headline genome and exome frequencies are what evidence work uses.
VARIANT_QUERY = """
query Variant($id: String!, $dataset: DatasetId!) {
  variant(variantId: $id, dataset: $dataset) {
    variant_id
    chrom
    pos
    ref
    alt
    genome { ac an af }
    exome { ac an af }
  }
}
"""


@dataclass
class GnomadRecord:
    """Allele frequencies for one variant in one gnomAD dataset."""

    variant_id: str
    dataset: str
    chrom: str
    pos: int
    ref: str
    alt: str
    genome_ac: Optional[int]  # allele count in genome samples
    genome_an: Optional[int]  # allele number (chromosomes sampled)
    genome_af: Optional[float]  # allele frequency
    exome_ac: Optional[int]
    exome_an: Optional[int]
    exome_af: Optional[float]
    source_url: str  # the variant's own page on gnomad.broadinstitute.org
    fetched_at: str
    from_cache: bool


def _counts(block: Optional[dict]) -> tuple[Optional[int], Optional[int], Optional[float]]:
    """Pull (ac, an, af) out of a genome or exome block.

    Either block can be missing entirely (a variant seen only in
    exomes, for example), which is a fact about the data, not an
    error, so it comes back as three Nones.
    """
    if not block:
        return None, None, None
    return block.get("ac"), block.get("an"), block.get("af")


def lookup_variant(
    variant_id: str,
    dataset: str = DEFAULT_DATASET,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 60.0,
) -> GnomadRecord:
    """Look up one variant, for example ``19-44908684-T-C``.

    Raises DataSourceError with kind ``not_found`` when gnomAD does
    not hold the variant in the named dataset, and the usual typed
    errors for HTTP, timeout, and parse failures. The gnomAD API can
    be slow on a cold query, so the default time budget here is
    longer than for the E-utilities clients.
    """
    body = json.dumps(
        {
            "query": VARIANT_QUERY,
            "variables": {"id": variant_id, "dataset": dataset},
        }
    ).encode("utf-8")
    fetched = fetch_text(
        SOURCE,
        f"gnomad:variant:{dataset}:{variant_id}",
        API_URL,
        body=body,
        headers={"Content-Type": "application/json"},
        cache=cache,
        transport=transport,
        timeout=timeout,
    )
    try:
        data = json.loads(fetched.text)
    except ValueError as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"answer was not JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise DataSourceError(SOURCE, "parse_error", "answer was not a JSON object")

    variant = (data.get("data") or {}).get("variant")
    if variant is None:
        # gnomAD answers an unknown variant with an errors list and a
        # null variant. "Variant not found" is a clean not_found.
        # Anything else in the errors list is the API telling us the
        # query itself was wrong, which is an api_error.
        messages = [
            str(error.get("message", ""))
            for error in (data.get("errors") or [])
            if isinstance(error, dict)
        ]
        if any("not found" in message.lower() for message in messages):
            raise DataSourceError(
                SOURCE, "not_found",
                f"variant {variant_id} is not in dataset {dataset}",
            )
        if messages:
            raise DataSourceError(SOURCE, "api_error", "; ".join(messages))
        raise DataSourceError(
            SOURCE, "parse_error", "answer had no variant and no error message"
        )

    try:
        genome_ac, genome_an, genome_af = _counts(variant.get("genome"))
        exome_ac, exome_an, exome_af = _counts(variant.get("exome"))
        return GnomadRecord(
            variant_id=variant["variant_id"],
            dataset=dataset,
            chrom=str(variant["chrom"]),
            pos=int(variant["pos"]),
            ref=variant["ref"],
            alt=variant["alt"],
            genome_ac=genome_ac,
            genome_an=genome_an,
            genome_af=genome_af,
            exome_ac=exome_ac,
            exome_an=exome_an,
            exome_af=exome_af,
            source_url=(
                f"https://gnomad.broadinstitute.org/variant/{variant_id}"
                f"?dataset={dataset}"
            ),
            fetched_at=fetched.fetched_at,
            from_cache=fetched.from_cache,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"variant record had an unexpected shape: {exc}"
        ) from exc
