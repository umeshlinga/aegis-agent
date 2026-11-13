"""Real-data layer: live clients for public bioinformatics sources.

Everything in this package follows the same rules. Records come from
the live public APIs (PubMed, ClinVar, gnomAD), never from fixtures or
the local test site. Every record carries the URL of its own page at
the source, so any citation built on it can be checked. Every failure
is a typed DataSourceError, never a silent empty result. Responses can
be cached on disk by the caller, and every record says whether it came
from a live fetch or the cache, and when the live fetch happened.
"""

from .cache import DiskCache
from .clinvar import ClinvarRecord, search_clinvar
from .errors import DataSourceError
from .gnomad import GnomadRecord, lookup_variant
from .pubmed import PubmedRecord, search_pubmed

__all__ = [
    "ClinvarRecord",
    "DataSourceError",
    "DiskCache",
    "GnomadRecord",
    "PubmedRecord",
    "lookup_variant",
    "search_clinvar",
    "search_pubmed",
]
