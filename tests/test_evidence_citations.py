"""Citation checker tests.

The fixtures here are hand built evidence items shaped exactly like
the items the dossier builder produces from the recorded real
records in the data layer tests. The dossier's returned_keys snapshot
is set by hand in each test to say which records the clients returned
in this run, which is what lets a fabricated item be fabricated: it
is an item whose key is not in the snapshot.

The one test that matters most: a plausible looking PubMed citation
to a record nobody fetched MUST be caught and removed.
"""

from aegis.evidence import EvidenceDossier, EvidenceItem, EvidenceQuery, check_citations


def pubmed_item(pmid="43015897", url=None):
    return EvidenceItem(
        source="pubmed",
        record_id=pmid,
        title="Patterns of metastasis according to germline BRCA1/2 mutation status",
        summary="Breast cancer research : BCR (2026). Recorded fixture article.",
        source_url=url or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        fetched_at="2026-11-13T19:27:28Z",
        from_cache=False,
    )


def clinvar_item(variation_id="5042465", url=None):
    return EvidenceItem(
        source="clinvar",
        record_id=variation_id,
        title="NM_007294.4(BRCA1):c.711del (p.Glu237fs)",
        summary="Classification: Pathogenic. Accession: VCV005042465.1.",
        source_url=url or f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{variation_id}/",
        fetched_at="2026-11-13T19:27:28Z",
        from_cache=False,
    )


def gnomad_item(variant_id="1-55516888-G-GA", url=None):
    return EvidenceItem(
        source="gnomad",
        record_id=variant_id,
        title=f"Population frequencies for variant {variant_id}",
        summary="Dataset gnomad_r2_1. Genome: allele frequency 0.00497.",
        source_url=url or (
            f"https://gnomad.broadinstitute.org/variant/{variant_id}?dataset=gnomad_r2_1"
        ),
        fetched_at="2026-11-13T19:28:45Z",
        from_cache=True,
    )


def dossier_of(items, returned=None):
    """A dossier whose snapshot says the given items were returned."""
    if returned is None:
        returned = items
    return EvidenceDossier(
        query=EvidenceQuery(gene="BRCA1"),
        items=list(items),
        returned_keys=tuple(item.key for item in returned),
    )


def test_real_shaped_items_from_all_sources_pass():
    items = [clinvar_item(), pubmed_item(), gnomad_item()]
    result = check_citations(dossier_of(items))
    assert result.verified == items
    assert result.failures == []


def test_fabricated_citation_is_caught_and_removed():
    # A clean looking citation to a real looking PMID, with a well
    # formed URL that matches the id. The one thing wrong with it:
    # no client returned this record in this run. That is the
    # hallucinated citation shape, and the checker must remove it.
    fabricated = pubmed_item(pmid="99999999")
    honest = [clinvar_item(), pubmed_item()]
    result = check_citations(dossier_of(honest + [fabricated], returned=honest))
    assert result.verified == honest
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.record_id == "99999999"
    assert "not returned by its client in this run" in failure.reason


def test_url_pointing_at_a_different_record_is_caught():
    # The item claims PMID 43015897 but its URL names another paper.
    mismatched = pubmed_item(url="https://pubmed.ncbi.nlm.nih.gov/11111111/")
    result = check_citations(dossier_of([mismatched]))
    assert result.verified == []
    assert "does not name PMID 43015897" in result.failures[0].reason


def test_clinvar_url_with_wrong_variation_is_caught():
    mismatched = clinvar_item(
        url="https://www.ncbi.nlm.nih.gov/clinvar/variation/123/"
    )
    result = check_citations(dossier_of([mismatched]))
    assert result.verified == []
    assert "does not name variation 5042465" in result.failures[0].reason


def test_gnomad_url_without_dataset_is_caught():
    no_dataset = gnomad_item(
        url="https://gnomad.broadinstitute.org/variant/1-55516888-G-GA"
    )
    result = check_citations(dossier_of([no_dataset]))
    assert result.verified == []
    assert "does not name its dataset" in result.failures[0].reason


def test_url_on_the_wrong_host_is_caught():
    wrong_host = pubmed_item(url="https://example.com/43015897/")
    result = check_citations(dossier_of([wrong_host]))
    assert result.verified == []
    assert "wrong" not in result.failures[0].reason  # reason names the hosts
    assert "example.com" in result.failures[0].reason


def test_malformed_url_is_caught():
    broken = pubmed_item(url="not a url at all")
    result = check_citations(dossier_of([broken]))
    assert result.verified == []
    assert "well formed" in result.failures[0].reason


def test_unknown_source_is_caught():
    mystery = EvidenceItem(
        source="wikipedia",
        record_id="BRCA1",
        title="BRCA1",
        summary="A page about a gene.",
        source_url="https://en.wikipedia.org/wiki/BRCA1",
        fetched_at="2026-11-13T19:27:28Z",
    )
    result = check_citations(dossier_of([mystery]))
    assert result.verified == []
    assert "unknown evidence source" in result.failures[0].reason


def test_verified_order_matches_dossier_order():
    items = [pubmed_item(), clinvar_item(), pubmed_item(pmid="43015421")]
    result = check_citations(dossier_of(items))
    assert result.verified == items
