"""Report renderer tests, plus the builder with stubbed transports.

The renderer tests build a small dossier by hand, run it through the
real citation checker and the real gate, and check the Markdown
carries the question, the verdict with its reason, the evidence with
its URLs, the removed citations, and the provenance footer.

The builder test replays the recorded real responses from the data
layer fixtures through stub transports, the same pattern as the data
tests, and confirms the dossier builder turns them into evidence
items whose citations pass the checker. No network anywhere here.
"""

import json
from pathlib import Path

from aegis.evidence import (
    EvidenceDossier,
    EvidenceItem,
    EvidenceQuery,
    build_evidence_dossier,
    check_citations,
    evaluate_gate,
    render_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "data"


def clinvar_item():
    return EvidenceItem(
        source="clinvar",
        record_id="5042465",
        title="NM_007294.4(BRCA1):c.711del (p.Glu237fs)",
        summary="Classification: Pathogenic. Accession: VCV005042465.1.",
        source_url="https://www.ncbi.nlm.nih.gov/clinvar/variation/5042465/",
        fetched_at="2026-11-13T19:27:28Z",
        from_cache=False,
    )


def pubmed_item(pmid, title, from_cache=True):
    return EvidenceItem(
        source="pubmed",
        record_id=pmid,
        title=title,
        summary="Breast cancer research : BCR (2026). An abstract.",
        source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        fetched_at="2026-11-13T19:27:28Z",
        from_cache=from_cache,
    )


def dossier_of(items, source_errors=None):
    return EvidenceDossier(
        query=EvidenceQuery(gene="BRCA1"),
        items=list(items),
        returned_keys=tuple(item.key for item in items),
        source_errors=list(source_errors or []),
    )


def render(dossier):
    check = check_citations(dossier)
    verdict = evaluate_gate(check.verified)
    return render_report(dossier, check, verdict)


def test_answerable_report_has_question_verdict_evidence_and_footer():
    items = [
        clinvar_item(),
        pubmed_item("43015897", "Patterns of metastasis according to germline status"),
        pubmed_item("43015421", "Saturation genome editing of BRCA1", from_cache=False),
    ]
    text = render(dossier_of(items))
    assert "# Evidence report: BRCA1" in text
    assert "**Answerable.**" in text
    assert "### ClinVar (1)" in text
    assert "### PubMed (2)" in text
    assert "https://www.ncbi.nlm.nih.gov/clinvar/variation/5042465/" in text
    assert "https://pubmed.ncbi.nlm.nih.gov/43015897/" in text
    # The footer records fetch times and cache status per source.
    assert "## Sources and provenance" in text
    assert "2026-11-13T19:27:28Z" in text
    assert "1 from cache, 1 from live fetch" in text  # the two PubMed items
    assert "0 from cache, 1 from live fetch" in text  # the ClinVar item


def test_declined_report_states_the_reason_and_still_shows_evidence():
    text = render(dossier_of([clinvar_item()]))
    assert "**Declined.**" in text
    assert "Missing:" in text
    # The thin evidence is shown so the gap is visible, not hidden.
    assert "NM_007294.4(BRCA1):c.711del" in text
    assert "No conclusion is drawn" in text


def test_removed_citations_and_failed_sources_appear_in_the_report():
    fabricated = pubmed_item("99999999", "A paper nobody fetched")
    items = [clinvar_item(), pubmed_item("43015897", "A real paper"), fabricated]
    dossier = dossier_of(items, source_errors=["gnomad: not_found: no variant"])
    # The snapshot says only the first two were actually returned.
    dossier.returned_keys = tuple(item.key for item in items[:2])
    text = render(dossier)
    assert "## Citations removed by the checker" in text
    assert "99999999" in text
    assert "not returned by its client in this run" in text
    assert "## Sources that did not answer" in text
    assert "gnomad: not_found" in text
    # The fabricated item's title must not appear as evidence. It is
    # named once in the removed section, and nowhere else.
    assert text.count("A paper nobody fetched") == 1


def test_empty_dossier_renders_a_clean_decline():
    text = render(dossier_of([]))
    assert "**Declined.**" in text
    assert "No evidence items passed citation checking." in text
    assert "No verified evidence" in text


# Builder, on the recorded real responses.


def _clinvar_transport(url, body, headers):
    if "esearch" in url:
        return (FIXTURES / "clinvar_esearch_brca1_gene.json").read_text(encoding="utf-8")
    if "esummary" in url:
        return (FIXTURES / "clinvar_esummary_5042465.json").read_text(encoding="utf-8")
    raise AssertionError(f"unexpected URL in test: {url}")


def _pubmed_transport(url, body, headers):
    if "esearch" in url:
        return (FIXTURES / "pubmed_esearch_brca1_variant.json").read_text(encoding="utf-8")
    if "efetch" in url:
        return (FIXTURES / "pubmed_efetch_43015897.xml").read_text(encoding="utf-8")
    raise AssertionError(f"unexpected URL in test: {url}")


def _dispatch_transport(url, body, headers):
    # One stub transport that routes by host, like the real run does
    # when both NCBI clients share the E-utilities base URL. The db
    # parameter in the query says which database answered.
    if "db=clinvar" in url:
        return _clinvar_transport(url, body, headers)
    return _pubmed_transport(url, body, headers)


def test_builder_turns_recorded_records_into_a_checked_dossier():
    dossier = build_evidence_dossier(
        "BRCA1", max_per_source=2, transport=_dispatch_transport
    )
    assert dossier.source_errors == []
    counts = dossier.count_by_source()
    assert counts["clinvar"] == 1
    assert counts["pubmed"] == 1
    # Every built item is in the returned snapshot, and checking the
    # built dossier removes nothing.
    assert set(dossier.returned_keys) == {item.key for item in dossier.items}
    check = check_citations(dossier)
    assert check.failures == []
    assert len(check.verified) == 2
    # Two items from two sources is below the item bar: a decline,
    # honestly, even though every citation is real.
    verdict = evaluate_gate(check.verified)
    assert verdict.answerable is False


def test_builder_records_a_failed_source_instead_of_hiding_it():
    def broken_transport(url, body, headers):
        from aegis.data.errors import DataSourceError

        raise DataSourceError("clinvar", "http_error", "HTTP 503 from test")

    dossier = build_evidence_dossier("BRCA1", transport=broken_transport)
    assert dossier.items == []
    assert len(dossier.source_errors) == 2  # clinvar and pubmed both failed
    assert all("http_error" in note for note in dossier.source_errors)


def test_gnomad_fixture_body_parses_as_json_for_the_builder_path():
    # Guard on the recorded gnomAD fixture staying valid: the builder
    # test above covers NCBI, and the data tests cover gnomAD itself.
    raw = (FIXTURES / "gnomad_variant_1_55516888_g_ga_r2_1.json").read_text(
        encoding="utf-8"
    )
    assert json.loads(raw)["data"]["variant"]["variant_id"] == "1-55516888-G-GA"
