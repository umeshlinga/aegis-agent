"""Web app tests: the product surface over the evidence core.

The dossier builder is stubbed with hand built dossiers (the same
fixture shaped items the evidence tests use), so the real citation
checker and the real decline gate run, but no test touches the
network. The server runs on an OS picked port, the way the explorer
tests do it.
"""

import threading
import urllib.error
import urllib.parse
import urllib.request

from aegis.evidence import EvidenceDossier, EvidenceItem, EvidenceQuery
from aegis.webapp.server import serve


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


def rich_builder(query):
    """A stub builder: plenty of verified evidence for any query."""
    items = [
        clinvar_item(),
        pubmed_item("43015897", "Patterns of metastasis according to germline status"),
        pubmed_item("43015421", "Saturation genome editing of BRCA1", from_cache=False),
    ]
    return EvidenceDossier(
        query=query,
        items=items,
        returned_keys=tuple(item.key for item in items),
    )


def thin_builder(query):
    """A stub builder: one item from one source, so the gate declines."""
    items = [pubmed_item("43015897", "One paper on its own")]
    return EvidenceDossier(
        query=query,
        items=items,
        returned_keys=tuple(item.key for item in items),
        source_errors=["gnomad: not_asked: no variant named in the query"],
    )


def start_server(build_dossier):
    server = serve(port=0, build_dossier=build_dossier)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"


def get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.read().decode("utf-8")


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return resp.status, resp.geturl(), resp.read().decode("utf-8")


def test_home_page_renders_the_form():
    server, base = start_server(rich_builder)
    try:
        status, text = get(base + "/")
        assert status == 200
        assert "Aegis" in text
        assert "evidence agent for genomic variant research" in text
        assert 'name="gene"' in text
        assert 'name="variant"' in text
        assert "No reports yet" in text
        assert "Local product prototype" in text
    finally:
        server.shutdown()


def test_answerable_result_page_shows_verdict_and_grouped_evidence():
    server, base = start_server(rich_builder)
    try:
        status, final_url, text = post_form(base + "/query", {"gene": "BRCA1", "variant": ""})
        assert status == 200
        assert final_url.endswith("/report/1")
        assert "Answerable" in text
        assert "ClinVar (1)" in text
        assert "PubMed (2)" in text
        assert "Saturation genome editing of BRCA1" in text
        assert "https://www.ncbi.nlm.nih.gov/clinvar/variation/5042465/" in text
        assert "live fetch" in text
        assert "from cache" in text
        assert "removed by the citation checker: 0" in text
    finally:
        server.shutdown()


def test_thin_dossier_shows_the_decline_reason():
    server, base = start_server(thin_builder)
    try:
        status, final_url, text = post_form(base + "/query", {"gene": "BRCA9", "variant": ""})
        assert status == 200
        assert final_url.endswith("/report/1")
        assert "Declined" in text
        assert "Missing:" in text
        assert "No conclusion is drawn from evidence this thin" in text
        assert "Sources that did not answer" in text
    finally:
        server.shutdown()


def test_validation_errors_rerender_the_form_with_a_message():
    server, base = start_server(rich_builder)
    try:
        status, final_url, text = post_form(base + "/query", {"gene": "", "variant": ""})
        assert status == 200
        assert final_url.endswith("/query") or final_url.endswith("/")
        assert "gene field cannot be empty" in text
        assert 'name="gene"' in text

        status, final_url, text = post_form(
            base + "/query", {"gene": "BRCA1", "variant": "not a variant"}
        )
        assert status == 200
        assert "does not look like a variant id" in text
        assert 'value="BRCA1"' in text
    finally:
        server.shutdown()


def test_history_lists_a_completed_run_and_links_back():
    server, base = start_server(rich_builder)
    try:
        post_form(base + "/query", {"gene": "BRCA1", "variant": ""})
        status, text = get(base + "/")
        assert status == 200
        assert 'href="/report/1"' in text
        assert "BRCA1" in text
        assert "Answerable" in text
        assert "No reports yet" not in text
    finally:
        server.shutdown()


def test_unknown_report_is_a_plain_404_page():
    server, base = start_server(rich_builder)
    try:
        try:
            get(base + "/report/99")
            raise AssertionError("an unknown report should 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert "Report not found" in exc.read().decode("utf-8")
    finally:
        server.shutdown()
