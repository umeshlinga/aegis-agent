"""The Aegis product web app: the evidence core with a face.

A small local web product, standard library only, in the same style
as the trace explorer. A person opens the home page, types a gene
(and optionally one variant), and gets a real evidence report: the
dossier is built from live PubMed, ClinVar, and gnomAD records by
the evidence core, every citation is checked, the decline gate is
applied, and the result page shows the verdict and the verified
evidence behind it, or declines in plain words.

Run it with:

    python -m aegis.webapp

then open http://127.0.0.1:8770/.
"""

from .server import ReportEntry, ReportHistory, FormError, run_query, serve

__all__ = [
    "FormError",
    "ReportEntry",
    "ReportHistory",
    "run_query",
    "serve",
]
