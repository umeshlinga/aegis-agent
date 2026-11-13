"""ClinVar client tests.

Stub transports replay recorded copies of real E-utilities responses
(tests/fixtures/data/, captured 2026-11-13). No network. The recorded
record is a real BRCA1 deletion classified Pathogenic, which makes the
expected values easy to check by eye against ClinVar itself.
"""

from pathlib import Path

import pytest

from aegis.data import DiskCache, search_clinvar
from aegis.data.clinvar import fetch_summaries, search_ids
from aegis.data.errors import DataSourceError

FIXTURES = Path(__file__).parent / "fixtures" / "data"
ESEARCH = (FIXTURES / "clinvar_esearch_brca1_gene.json").read_text(encoding="utf-8")
ESUMMARY = (FIXTURES / "clinvar_esummary_5042465.json").read_text(encoding="utf-8")


def recorded_transport(calls=None):
    def _send(url, body, headers):
        if calls is not None:
            calls.append(url)
        if "esearch" in url:
            return ESEARCH
        if "esummary" in url:
            return ESUMMARY
        raise AssertionError(f"unexpected URL in test: {url}")

    return _send


def test_search_ids_reads_the_recorded_esearch():
    ids = search_ids("BRCA1[gene]", max_results=2, transport=recorded_transport())
    assert ids == ["5042465", "5042298"]


def test_fetch_summaries_parses_the_recorded_record():
    records = fetch_summaries(["5042465"], transport=recorded_transport())
    assert len(records) == 1
    record = records[0]
    assert record.variation_id == "5042465"
    assert record.accession == "VCV005042465.1"
    assert record.title == "NM_007294.4(BRCA1):c.711del (p.Glu237fs)"
    assert record.classification == "Pathogenic"
    assert record.review_status == "criteria provided, single submitter"
    assert record.last_evaluated.startswith("2026/09/18")
    assert "Breast-ovarian cancer" in record.trait
    assert record.source_url == "https://www.ncbi.nlm.nih.gov/clinvar/variation/5042465/"
    assert record.from_cache is False
    assert record.fetched_at


def test_search_clinvar_chains_search_and_summary():
    records = search_clinvar("BRCA1[gene]", max_results=2, transport=recorded_transport())
    assert [r.variation_id for r in records] == ["5042465"]
    assert records[0].classification == "Pathogenic"


def test_second_run_comes_from_cache(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    first = fetch_summaries(["5042465"], cache=cache, transport=recorded_transport())
    assert first[0].from_cache is False

    def failing_transport(url, body, headers):
        raise AssertionError("the network must not be touched on a cache hit")

    second = fetch_summaries(["5042465"], cache=cache, transport=failing_transport)
    assert second[0].from_cache is True
    assert second[0].fetched_at == first[0].fetched_at
    assert second[0].accession == first[0].accession


def test_http_failure_is_a_typed_error():
    def broken_transport(url, body, headers):
        raise DataSourceError("clinvar", "http_error", "HTTP 503 from test")

    with pytest.raises(DataSourceError) as caught:
        search_ids("BRCA1[gene]", transport=broken_transport)
    assert caught.value.kind == "http_error"
    assert caught.value.source == "clinvar"


def test_garbage_answer_is_a_parse_error():
    def garbage_transport(url, body, headers):
        return '{"result": "not the shape anyone promised"}'

    with pytest.raises(DataSourceError) as caught:
        fetch_summaries(["5042465"], transport=garbage_transport)
    assert caught.value.kind == "parse_error"
