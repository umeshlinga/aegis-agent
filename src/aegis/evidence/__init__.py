"""Evidence core: dossiers from live records, checked, gated, reported.

The pipeline, in the order a run flows through it:

1. ``dossier`` gathers evidence items from the real data clients
   (ClinVar, PubMed, gnomAD) for one gene or variant question, and
   snapshots which records the clients actually returned.
2. ``citations`` verifies every item against its source record and
   that snapshot. Anything that does not pass is removed and logged.
3. ``gate`` applies the decline rule to the verified items only: at
   least 3 items from at least 2 different sources, or the answer is
   a decline that states what is missing.
4. ``report`` renders the outcome as a Markdown report a person can
   read and defend.

No LLM is involved in this layer, and none is needed: gathering,
checking, gating, and reporting are all code over real records. A
model summary may later be written on top of a verified, answerable
dossier, and it will only ever see items that passed this pipeline.
"""

from .citations import CitationCheckResult, CitationFailure, check_citations
from .dossier import (
    EvidenceDossier,
    EvidenceItem,
    EvidenceQuery,
    build_evidence_dossier,
    parse_query,
)
from .gate import GateVerdict, evaluate_gate
from .report import render_report

__all__ = [
    "CitationCheckResult",
    "CitationFailure",
    "EvidenceDossier",
    "EvidenceItem",
    "EvidenceQuery",
    "GateVerdict",
    "build_evidence_dossier",
    "check_citations",
    "evaluate_gate",
    "parse_query",
    "render_report",
]
