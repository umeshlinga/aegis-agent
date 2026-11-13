"""Decline gate tests.

The bar: at least 3 verified items from at least 2 different
sources. Each boundary is tested on both sides, and the reason string
is checked for the actual numbers, because the reason is the part a
user reads when an answer is declined.
"""

from aegis.evidence import EvidenceItem, evaluate_gate
from aegis.evidence.gate import MIN_ITEMS, MIN_SOURCES


def item(source, record_id):
    return EvidenceItem(
        source=source,
        record_id=record_id,
        title=f"{source} record {record_id}",
        summary="Fixture item for gate tests.",
        source_url="https://example.invalid/record",
        fetched_at="2026-11-13T19:27:28Z",
    )


def test_bar_constants_are_the_documented_ones():
    assert MIN_ITEMS == 3
    assert MIN_SOURCES == 2


def test_three_items_from_two_sources_is_answerable():
    verdict = evaluate_gate(
        [item("clinvar", "1"), item("clinvar", "2"), item("pubmed", "3")]
    )
    assert verdict.answerable is True
    assert verdict.item_count == 3
    assert verdict.source_count == 2
    assert verdict.sources == ("clinvar", "pubmed")
    assert "3 verified evidence items from 2 different sources" in verdict.reason


def test_more_than_the_bar_is_answerable():
    verdict = evaluate_gate(
        [
            item("clinvar", "1"),
            item("pubmed", "2"),
            item("pubmed", "3"),
            item("gnomad", "4"),
        ]
    )
    assert verdict.answerable is True
    assert verdict.source_count == 3


def test_two_items_from_two_sources_declines_on_count():
    verdict = evaluate_gate([item("clinvar", "1"), item("pubmed", "2")])
    assert verdict.answerable is False
    assert "Missing: 1 more verified item." in verdict.reason


def test_three_items_from_one_source_declines_on_sources():
    verdict = evaluate_gate(
        [item("pubmed", "1"), item("pubmed", "2"), item("pubmed", "3")]
    )
    assert verdict.answerable is False
    assert verdict.item_count == 3
    assert verdict.source_count == 1
    assert "evidence from 1 more source" in verdict.reason


def test_empty_dossier_declines_and_names_both_gaps():
    verdict = evaluate_gate([])
    assert verdict.answerable is False
    assert verdict.item_count == 0
    assert verdict.source_count == 0
    assert "3 more verified items" in verdict.reason
    assert "2 more sources" in verdict.reason


def test_one_item_declines():
    verdict = evaluate_gate([item("clinvar", "1")])
    assert verdict.answerable is False
    assert "1 verified evidence item from 1 source" in verdict.reason
