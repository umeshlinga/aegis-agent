"""PubMed client, on the NCBI E-utilities API.

Two calls do the work. ``esearch`` turns a search term into a list of
PubMed ids, and ``efetch`` returns the full records for those ids as
XML. This client parses the XML into plain records: PMID, title,
abstract, journal, and year. Every record carries the URL of its own
PubMed page, because a citation the product shows has to point at the
exact page a person (or the citation checker) can open to verify it.

API reference: https://www.ncbi.nlm.nih.gov/books/NBK25501/
No API key is required. NCBI asks anonymous callers to stay at a few
requests per second, which the shape of this client (two requests per
search) respects easily.
"""

from __future__ import annotations

import json
import urllib.parse
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Optional

from .cache import DiskCache
from .errors import DataSourceError
from .http import Transport, fetch_text

SOURCE = "pubmed"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass
class PubmedRecord:
    """One PubMed article, with provenance for citation checking."""

    pmid: str
    title: str
    abstract: str
    journal: str
    year: str
    source_url: str  # the article's own page on pubmed.ncbi.nlm.nih.gov
    fetched_at: str  # when the record text was fetched live (ISO 8601 UTC)
    from_cache: bool  # True when this copy came from the local cache


def _parse_json(source: str, text: str, what: str) -> dict:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise DataSourceError(
            source, "parse_error", f"{what} answer was not JSON: {exc}"
        ) from exc


def search_ids(
    term: str,
    max_results: int = 10,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> list[str]:
    """Run one esearch and return the matching PubMed ids, best first."""
    query = urllib.parse.urlencode(
        {"db": "pubmed", "term": term, "retmax": max_results, "retmode": "json"}
    )
    url = f"{EUTILS_BASE}/esearch.fcgi?{query}"
    fetched = fetch_text(
        SOURCE, f"pubmed:esearch:{term}:{max_results}", url,
        cache=cache, transport=transport, timeout=timeout,
    )
    data = _parse_json(SOURCE, fetched.text, "esearch")
    try:
        return list(data["esearchresult"]["idlist"])
    except (KeyError, TypeError) as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"esearch answer had no id list: {exc}"
        ) from exc


def _article_text(article: ElementTree.Element, path: str) -> str:
    node = article.find(path)
    if node is None:
        return ""
    # Titles and abstracts can contain inline markup (italics for gene
    # names, for example). itertext keeps the words and drops the tags.
    return "".join(node.itertext()).strip()


def _parse_articles(xml_text: str, fetched_at: str, from_cache: bool) -> list[PubmedRecord]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise DataSourceError(
            SOURCE, "parse_error", f"efetch answer was not XML: {exc}"
        ) from exc
    records = []
    for article in root.iter("PubmedArticle"):
        pmid = _article_text(article, ".//PMID")
        if not pmid:
            continue
        # An abstract can arrive as several labelled parts (Background,
        # Methods, Results). Join them in order with their labels.
        abstract_parts = []
        for node in article.findall(".//Abstract/AbstractText"):
            label = node.get("Label")
            words = "".join(node.itertext()).strip()
            if words:
                abstract_parts.append(f"{label}: {words}" if label else words)
        records.append(
            PubmedRecord(
                pmid=pmid,
                title=_article_text(article, ".//ArticleTitle"),
                abstract="\n".join(abstract_parts),
                journal=_article_text(article, ".//Journal/Title"),
                year=_article_text(article, ".//JournalIssue/PubDate/Year"),
                source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                fetched_at=fetched_at,
                from_cache=from_cache,
            )
        )
    return records


def fetch_records(
    pmids: list[str],
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> list[PubmedRecord]:
    """Fetch full records for a list of PubMed ids with one efetch."""
    if not pmids:
        return []
    query = urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    )
    url = f"{EUTILS_BASE}/efetch.fcgi?{query}"
    fetched = fetch_text(
        SOURCE, f"pubmed:efetch:{','.join(pmids)}", url,
        cache=cache, transport=transport, timeout=timeout,
    )
    return _parse_articles(fetched.text, fetched.fetched_at, fetched.from_cache)


def search_pubmed(
    term: str,
    max_results: int = 10,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> list[PubmedRecord]:
    """Search PubMed and return full records for the top matches.

    An empty list means the search genuinely matched nothing. A failed
    request raises DataSourceError instead, never an empty list.
    """
    pmids = search_ids(
        term, max_results=max_results, cache=cache,
        transport=transport, timeout=timeout,
    )
    return fetch_records(pmids, cache=cache, transport=transport, timeout=timeout)
