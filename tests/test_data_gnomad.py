"""gnomAD client tests.

Stub transports replay recorded copies of real GraphQL responses
(tests/fixtures/data/, captured 2026-11-13). No network. Two recorded
answers matter most: a real variant record from gnomad_r2_1, and the
real not-found answer the same variant gets from gnomad_r4, which the
client must surface as a typed not_found error, never as a frequency
of zero.
"""

import json
from pathlib import Path

import pytest

from aegis.data import DiskCache, lookup_variant
from aegis.data.errors import DataSourceError

FIXTURES = Path(__file__).parent / "fixtures" / "data"
FOUND = (FIXTURES / "gnomad_variant_1_55516888_g_ga_r2_1.json").read_text(encoding="utf-8")
NOT_FOUND = (FIXTURES / "gnomad_variant_not_found_r4.json").read_text(encoding="utf-8")


def fixed_transport(response_text, calls=None):
    def _send(url, body, headers):
        if calls is not None:
            calls.append((url, body, headers))
        assert url == "https://gnomad.broadinstitute.org/api"
        assert headers["Content-Type"] == "application/json"
        sent = json.loads(body.decode("utf-8"))
        assert sent["variables"]["id"] == "1-55516888-G-GA"
        return response_text

    return _send


def test_lookup_parses_the_recorded_variant():
    record = lookup_variant(
        "1-55516888-G-GA", dataset="gnomad_r2_1", transport=fixed_transport(FOUND)
    )
    assert record.variant_id == "1-55516888-G-GA"
    assert record.dataset == "gnomad_r2_1"
    assert (record.chrom, record.pos, record.ref, record.alt) == ("1", 55516888, "G", "GA")
    assert record.genome_ac == 156
    assert record.genome_an == 31382
    assert record.genome_af == pytest.approx(0.004971002485501243)
    assert record.exome_ac == 121
    assert record.exome_an == 128230
    assert record.exome_af == pytest.approx(0.0009436169383139671)
    assert record.source_url == (
        "https://gnomad.broadinstitute.org/variant/1-55516888-G-GA?dataset=gnomad_r2_1"
    )
    assert record.from_cache is False
    assert record.fetched_at


def test_unknown_variant_is_a_typed_not_found():
    with pytest.raises(DataSourceError) as caught:
        lookup_variant(
            "1-55516888-G-GA", dataset="gnomad_r4",
            transport=fixed_transport(NOT_FOUND),
        )
    assert caught.value.kind == "not_found"
    assert caught.value.source == "gnomad"


def test_second_lookup_comes_from_cache(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    first = lookup_variant(
        "1-55516888-G-GA", dataset="gnomad_r2_1",
        cache=cache, transport=fixed_transport(FOUND),
    )
    assert first.from_cache is False

    def failing_transport(url, body, headers):
        raise AssertionError("the network must not be touched on a cache hit")

    second = lookup_variant(
        "1-55516888-G-GA", dataset="gnomad_r2_1",
        cache=cache, transport=failing_transport,
    )
    assert second.from_cache is True
    assert second.fetched_at == first.fetched_at
    assert second.genome_af == first.genome_af


def test_timeout_is_a_typed_error():
    def slow_transport(url, body, headers):
        raise DataSourceError("gnomad", "timeout", "no answer within 60 seconds")

    with pytest.raises(DataSourceError) as caught:
        lookup_variant("1-55516888-G-GA", transport=slow_transport)
    assert caught.value.kind == "timeout"


def test_garbage_answer_is_a_parse_error():
    with pytest.raises(DataSourceError) as caught:
        lookup_variant(
            "1-55516888-G-GA", transport=fixed_transport("not json at all")
        )
    assert caught.value.kind == "parse_error"


def test_wrong_shape_variant_is_a_parse_error():
    wrong_shape = json.dumps({"data": {"variant": {"variant_id": "x"}}})
    with pytest.raises(DataSourceError) as caught:
        lookup_variant("1-55516888-G-GA", transport=fixed_transport(wrong_shape))
    assert caught.value.kind == "parse_error"
