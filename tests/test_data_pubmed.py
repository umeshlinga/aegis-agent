"""PubMed client tests.

Transports are stubbed with recorded copies of real E-utilities
responses (tests/fixtures/data/, captured 2026-11-13). No test here
touches the network. The point of each test: the parser pulls the
right fields out of the real response shapes, every record carries a
verifiable source URL and provenance, a second run is served from the
cache, and failures surface as typed errors instead of empty results.
"""

from pathlib import Path

import pytest

from aegis.data import DiskCache, search_pubmed
from aegis.data.errors import DataSourceError
from aegis.data.pubmed import fetch_records, search_ids

FIXTURES = Path(__file__).parent / "fixtures" / "data"
ESEARCH = (FIXTURES / "pubmed_esearch_brca1_variant.json").read_text(encoding="utf-8")
EFETCH = (FIXTURES / "pubmed_efetch_43015897.xml").read_text(encoding="utf-8")


def recorded_transport(calls=None):
    """A transport that answers from the recorded fixtures."""

    def _send(url, body, headers):
        if calls is not None:
            calls.append(url)
        if "esearch" in url:
            return ESEARCH
        if "efetch" in url:
            return EFETCH
        raise AssertionError(f"unexpected URL in test: {url}")

    return _send


def test_search_ids_reads_the_recorded_esearch():
    ids = search_ids("BRCA1 variant", max_results=2, transport=recorded_transport())
    assert ids == ["43015897", "43015421"]


def test_fetch_records_parses_the_recorded_article():
    records = fetch_records(["43015897"], transport=recorded_transport())
    assert len(records) == 1
    record = records[0]
    assert record.pmid == "43015897"
    assert record.title.startswith("Patterns of metastasis according to germline")
    assert "BRCA1/2 pathogenic variants" in record.abstract
    assert record.journal == "Breast cancer research : BCR"
    assert record.year == "2026"
    assert record.source_url == "https://pubmed.ncbi.nlm.nih.gov/43015897/"
    assert record.from_cache is False
    assert record.fetched_at  # a live (stubbed) fetch is always timestamped


def test_search_pubmed_chains_search_and_fetch():
    records = search_pubmed("BRCA1 variant", max_results=2, transport=recorded_transport())
    # The efetch fixture holds only the first article; the client
    # returns what the fetch returned, in order.
    assert [r.pmid for r in records] == ["43015897"]


def test_second_run_comes_from_cache(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    calls = []
    first = fetch_records(["43015897"], cache=cache, transport=recorded_transport(calls))
    assert first[0].from_cache is False
    assert len(calls) == 1

    def failing_transport(url, body, headers):
        raise AssertionError("the network must not be touched on a cache hit")

    second = fetch_records(["43015897"], cache=cache, transport=failing_transport)
    assert second[0].from_cache is True
    assert second[0].fetched_at == first[0].fetched_at
    assert second[0].title == first[0].title


def test_http_failure_is_a_typed_error():
    def broken_transport(url, body, headers):
        raise DataSourceError("pubmed", "http_error", "HTTP 500 from test")

    with pytest.raises(DataSourceError) as caught:
        search_ids("BRCA1 variant", transport=broken_transport)
    assert caught.value.kind == "http_error"
    assert caught.value.source == "pubmed"


def test_timeout_is_a_typed_error_not_an_empty_result():
    def slow_transport(url, body, headers):
        raise DataSourceError("pubmed", "timeout", "no answer within 30 seconds")

    with pytest.raises(DataSourceError) as caught:
        search_pubmed("BRCA1 variant", transport=slow_transport)
    assert caught.value.kind == "timeout"


def test_garbage_search_answer_is_a_parse_error():
    def garbage_transport(url, body, headers):
        return "this is not json"

    with pytest.raises(DataSourceError) as caught:
        search_ids("BRCA1 variant", transport=garbage_transport)
    assert caught.value.kind == "parse_error"


def test_garbage_article_answer_is_a_parse_error():
    def garbage_transport(url, body, headers):
        return "<PubmedArticleSet><unclosed>"

    with pytest.raises(DataSourceError) as caught:
        fetch_records(["43015897"], transport=garbage_transport)
    assert caught.value.kind == "parse_error"
