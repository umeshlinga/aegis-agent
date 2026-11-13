"""A small on-disk cache for data source responses.

Why this exists: evidence runs have to be reproducible. If the same
question is run twice, the second run should be able to show exactly
what the first run saw, and the trace should be able to say whether a
record came from a live fetch or from the cache, and when the live
fetch happened. Public sources also change over time (ClinVar
reclassifies variants), so "when was this fetched" is part of the
evidence, not a detail.

The cache is deliberately boring: one JSON file per request, named by
the SHA-256 of a caller supplied request key, holding the request key,
the fetch timestamp, and the raw response text. No database, no expiry
policy. Freshness decisions belong to the caller, who can read the
timestamp off every record and decide whether it is recent enough.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class CachedResponse:
    """One cached response body plus when it was fetched live."""

    response_text: str
    fetched_at: str  # ISO 8601 UTC, the moment of the live fetch


def utc_now_iso() -> str:
    """Current UTC time as an ISO 8601 string, seconds precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DiskCache:
    """File-per-entry cache under a caller chosen directory."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path_for(self, request_key: str) -> Path:
        digest = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def lookup(self, request_key: str) -> Optional[CachedResponse]:
        """Return the cached response for a request key, or None.

        A cache file that exists but cannot be read or parsed is
        treated as a miss, not an error: the caller will simply fetch
        live. Losing a cache entry must never break a run.
        """
        path = self._path_for(request_key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            return CachedResponse(
                response_text=entry["response_text"],
                fetched_at=entry["fetched_at"],
            )
        except (ValueError, KeyError, OSError):
            return None

    def store(self, request_key: str, response_text: str) -> str:
        """Store a live response and return its fetch timestamp."""
        fetched_at = utc_now_iso()
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {
            "request_key": request_key,
            "fetched_at": fetched_at,
            "response_text": response_text,
        }
        self._path_for(request_key).write_text(
            json.dumps(entry, indent=2), encoding="utf-8"
        )
        return fetched_at
