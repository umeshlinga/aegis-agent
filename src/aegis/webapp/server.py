"""The web app server: form in, evidence report out.

Standard library only, following the trace explorer's pattern: a
ThreadingHTTPServer, a handler built by a factory so tests can hand
it a stub dossier builder, and no framework anywhere.

Routes:

  GET  /            the home page (form plus this session's history)
  POST /query       validate the form, run the evidence core, then
                    redirect to the new report's page
  GET  /report/<id> one stored report from this session

State is exactly one thing: the ReportHistory, an in-memory list of
the reports this server process has produced. There is no database
and there are no accounts; when the process stops, the history is
gone, and the pages say so.

Failure handling is part of the product, not an afterthought.
Validation problems re-render the form with the problem stated in
plain words. A source that fails inside the evidence core arrives
as a note on the report page ("gnomAD did not answer"), because the
dossier builder records source failures instead of raising them. A
failure outside that shape, for example the builder itself raising,
is caught here and shown as a clear message, never as a stack trace.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..data import DiskCache
from ..data.cache import utc_now_iso
from ..evidence import (
    EvidenceDossier,
    EvidenceQuery,
    build_evidence_dossier,
    check_citations,
    evaluate_gate,
)
from ..evidence.citations import CitationCheckResult
from ..evidence.gate import GateVerdict
from . import pages

# A gnomAD variant id: chrom, position, ref, alt. HGVS strings are
# the other shape the evidence query parser meets in practice.
_GNOMAD_VARIANT = re.compile(r"^[0-9XY]+-\d+-[A-Za-z]+-[A-Za-z]+$")
_HGVS_VARIANT = re.compile(r"^[cp]\.[A-Za-z0-9_.*>+\-\(\)\[\]]+$")
_GENE_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*$")

VARIANT_HINT = (
    "That variant does not look like a variant id. Use the gnomAD "
    "form chrom-position-ref-alt, for example 17-43044295-G-A, or an "
    "HGVS form like c.711del or p.Glu237fs, or leave the variant "
    "field blank."
)


class FormError(ValueError):
    """A form problem, stated in words a user can act on."""


@dataclass
class ReportEntry:
    """One finished run: query, checked dossier, verdict, and when."""

    entry_id: int
    query: EvidenceQuery
    dossier: EvidenceDossier
    check: CitationCheckResult
    verdict: GateVerdict
    created_at: str


class ReportHistory:
    """The reports of one server session, newest first for display."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ReportEntry] = []
        self._next_id = 1

    def add(self, query, dossier, check, verdict) -> ReportEntry:
        with self._lock:
            entry = ReportEntry(
                entry_id=self._next_id,
                query=query,
                dossier=dossier,
                check=check,
                verdict=verdict,
                created_at=utc_now_iso(),
            )
            self._next_id += 1
            self._entries.append(entry)
            return entry

    def get(self, entry_id: int) -> Optional[ReportEntry]:
        with self._lock:
            for entry in self._entries:
                if entry.entry_id == entry_id:
                    return entry
        return None

    def newest_first(self) -> list[ReportEntry]:
        with self._lock:
            return list(reversed(self._entries))


def validate_form(gene_text: str, variant_text: str) -> EvidenceQuery:
    """Turn raw form fields into an EvidenceQuery, or say what is wrong.

    The gene is one symbol with no spaces. The variant, when given,
    must be a shape the evidence core can look up: a gnomAD variant
    id or an HGVS string. Anything else is refused here, in words,
    rather than sent to the sources to be misread quietly.
    """
    gene = (gene_text or "").strip()
    variant = (variant_text or "").strip()
    if not gene:
        raise FormError(
            "Please type a gene symbol, for example BRCA1. "
            "The gene field cannot be empty."
        )
    if not _GENE_SYMBOL.match(gene):
        raise FormError(
            "The gene should be one symbol with no spaces, "
            "for example BRCA1 or TP53."
        )
    if variant and not (_GNOMAD_VARIANT.match(variant) or _HGVS_VARIANT.match(variant)):
        raise FormError(VARIANT_HINT)
    return EvidenceQuery(gene=gene, variant=variant or None)


# A dossier builder takes a query and returns a dossier. The real one
# is the evidence core's builder with a cache bound in; tests pass a
# stub, so no test touches the network.
DossierBuilder = Callable[[EvidenceQuery], EvidenceDossier]


def run_query(
    history: ReportHistory,
    gene_text: str,
    variant_text: str,
    build_dossier: DossierBuilder,
) -> ReportEntry:
    """Validate, build, check, gate, and store one report.

    Raises FormError for validation problems. Anything the builder
    raises propagates to the handler, which turns it into a plain
    message page; per-source failures never reach here as exceptions
    because the builder records them on the dossier.
    """
    query = validate_form(gene_text, variant_text)
    dossier = build_dossier(query)
    check = check_citations(dossier)
    verdict = evaluate_gate(check.verified)
    return history.add(query, dossier, check, verdict)


def _default_builder(cache: Optional[DiskCache]) -> DossierBuilder:
    def build(query: EvidenceQuery) -> EvidenceDossier:
        return build_evidence_dossier(query, cache=cache)

    return build


def make_handler(history: ReportHistory, build_dossier: DossierBuilder):
    class WebappHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass  # keep the console clean; this is a local product

        def _send_html(self, body_text: str, status: int = 200) -> None:
            body = body_text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _home(self, error: str | None = None, gene: str = "", variant: str = "") -> None:
            self._send_html(
                pages.render_home(
                    history.newest_first(), error=error, gene=gene, variant=variant
                )
            )

        def do_GET(self) -> None:  # noqa: N802 (stdlib handler API)
            path = urlparse(self.path).path
            if path == "/":
                self._home()
                return
            if path.startswith("/report/"):
                raw_id = path[len("/report/"):].strip("/")
                entry = history.get(int(raw_id)) if raw_id.isdigit() else None
                if entry is None:
                    self._send_html(
                        pages.render_message(
                            "Report not found",
                            "That report is not in this session's history. "
                            "It may belong to an earlier run of the server, "
                            "and history is kept in memory only.",
                        ),
                        status=404,
                    )
                else:
                    self._send_html(pages.render_result(entry))
                return
            self._send_html(
                pages.render_message(
                    "Page not found",
                    "There is no page at this address. The home page "
                    "has the question form and this session's reports.",
                ),
                status=404,
            )

        def do_POST(self) -> None:  # noqa: N802 (stdlib handler API)
            path = urlparse(self.path).path
            if path != "/query":
                self._send_html(
                    pages.render_message(
                        "Page not found",
                        "There is nothing to submit to at this address.",
                    ),
                    status=404,
                )
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            fields = parse_qs(raw, keep_blank_values=True)
            gene = fields.get("gene", [""])[0]
            variant = fields.get("variant", [""])[0]
            try:
                entry = run_query(history, gene, variant, build_dossier)
            except FormError as exc:
                # Re-render the form with the problem and the values
                # as typed, so fixing the input is one edit, not a
                # retype.
                self._home(error=str(exc), gene=gene, variant=variant)
                return
            except Exception:
                # The builder records per-source failures on the
                # dossier, so an exception here is outside that shape
                # (for example the cache directory is unusable). Say
                # so plainly; a stack trace helps nobody reading a
                # product page.
                self._home(
                    error=(
                        "The evidence run failed before a report could "
                        "be built. The data sources may be unreachable "
                        "from this machine. Please try again in a moment."
                    ),
                    gene=gene,
                    variant=variant,
                )
                return
            self._redirect(f"/report/{entry.entry_id}")

    return WebappHandler


def serve(
    port: int = 8770,
    cache: Optional[DiskCache] = None,
    build_dossier: Optional[DossierBuilder] = None,
) -> ThreadingHTTPServer:
    """Build the server. Port 0 lets the OS pick, which tests use."""
    history = ReportHistory()
    builder = build_dossier if build_dossier is not None else _default_builder(cache)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(history, builder))
    return server
