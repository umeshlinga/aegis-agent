"""ClinVar client, on the NCBI E-utilities API.

ClinVar is the public archive of how variants have been classified
(pathogenic, benign, uncertain significance, and the rest) and by
whom. It is the ground truth source for most of the evidence work this
product does, and it changes over time as variants are reclassified,
which is why every record here carries its fetch timestamp.

Two calls: ``esearch`` on the clinvar database turns a gene or variant
query into variation ids, and ``esummary`` returns the record for each
id: accession, title (the variant name in HGVS form), the germline
classification, its review status, and when it was last evaluated.
Every record carries the URL of its own ClinVar variation page.

API reference: https://www.ncbi.nlm.nih.gov/books/NBK25501/
No API key is required.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from .cache import DiskCache
from .errors import DataSourceError
from .http import Transport, fetch_text

SOURCE = "clinvar"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass
class ClinvarRecord:
    """One ClinVar variation record, with provenance."""

    variation_id: str
    accession: str  # versioned accession, for example VCV005042465.1
    title: str  # variant name as ClinVar titles it, usually HGVS
    classification: str  # germline classification, for example Pathogenic
    review_status: str  # how much review stands behind the classification
    last_evaluated: str  # date ClinVar last evaluated the classification
    trait: str  # first condition named on the record, when there is one
    source_url: str  # the record's own page on ncbi.nlm.nih.gov/clinvar
    fetched_at: str
    from_cache: bool


def _parse_json(text: str, what: str) -> dict:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"{what} answer was not JSON: {exc}"
        ) from exc


def search_ids(
    query_text: str,
    max_results: int = 10,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> list[str]:
    """Run one esearch on the clinvar database, return variation ids.

    The query is passed to ClinVar as written, so callers can use
    ClinVar's own field syntax, for example ``BRCA1[gene]`` or a
    variant name in quotes.
    """
    query = urllib.parse.urlencode(
        {"db": "clinvar", "term": query_text, "retmax": max_results, "retmode": "json"}
    )
    url = f"{EUTILS_BASE}/esearch.fcgi?{query}"
    fetched = fetch_text(
        SOURCE, f"clinvar:esearch:{query_text}:{max_results}", url,
        cache=cache, transport=transport, timeout=timeout,
    )
    data = _parse_json(fetched.text, "esearch")
    try:
        return list(data["esearchresult"]["idlist"])
    except (KeyError, TypeError) as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"esearch answer had no id list: {exc}"
        ) from exc


def _one_record(uid: str, raw: dict, fetched_at: str, from_cache: bool) -> ClinvarRecord:
    germline = raw.get("germline_classification") or {}
    traits = germline.get("trait_set") or []
    return ClinvarRecord(
        variation_id=uid,
        accession=raw.get("accession_version") or raw.get("accession") or "",
        title=raw.get("title") or "",
        classification=germline.get("description") or "",
        review_status=germline.get("review_status") or "",
        last_evaluated=germline.get("last_evaluated") or "",
        trait=traits[0].get("trait_name", "") if traits else "",
        source_url=f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/",
        fetched_at=fetched_at,
        from_cache=from_cache,
    )


def fetch_summaries(
    variation_ids: list[str],
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> list[ClinvarRecord]:
    """Fetch the esummary record for each variation id."""
    if not variation_ids:
        return []
    query = urllib.parse.urlencode(
        {"db": "clinvar", "id": ",".join(variation_ids), "retmode": "json"}
    )
    url = f"{EUTILS_BASE}/esummary.fcgi?{query}"
    fetched = fetch_text(
        SOURCE, f"clinvar:esummary:{','.join(variation_ids)}", url,
        cache=cache, transport=transport, timeout=timeout,
    )
    data = _parse_json(fetched.text, "esummary")
    try:
        result = data["result"]
        uids = result["uids"]
    except (KeyError, TypeError) as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"esummary answer had no result set: {exc}"
        ) from exc
    records = []
    for uid in uids:
        raw = result.get(str(uid))
        if raw is None:
            raise DataSourceError(
                SOURCE, "parse_error", f"esummary answer was missing id {uid}"
            )
        records.append(_one_record(str(uid), raw, fetched.fetched_at, fetched.from_cache))
    return records


def search_clinvar(
    query_text: str,
    max_results: int = 10,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> list[ClinvarRecord]:
    """Search ClinVar and return full summary records for the matches.

    An empty list means the query genuinely matched nothing. A failed
    request raises DataSourceError instead, never an empty list.
    """
    ids = search_ids(
        query_text, max_results=max_results, cache=cache,
        transport=transport, timeout=timeout,
    )
    return fetch_summaries(ids, cache=cache, transport=transport, timeout=timeout)
