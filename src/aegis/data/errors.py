"""Typed errors for the real-data layer.

Every client in this package fails the same way: it raises
DataSourceError with a ``kind`` and the name of the source that failed.
A caller (the agent core, the trace writer, a test) can read the kind
and decide what to do. What a client never does is return an empty list
or a None and let the caller guess whether the source had nothing or
the request simply failed. In evidence work that difference matters:
"no records found" is an answer, "the request failed" is not.
"""

from __future__ import annotations


class DataSourceError(Exception):
    """One data source failed in one classifiable way.

    Kinds:
    - ``http_error``: the source answered with an HTTP error status.
    - ``timeout``: the request ran past its time budget.
    - ``network_error``: the request never reached the source (DNS,
      connection refused, connection reset).
    - ``parse_error``: the source answered, but the body was not the
      shape the client expected.
    - ``not_found``: the source answered and said the thing asked for
      does not exist in it (gnomAD returns this for unknown variants).
    - ``api_error``: the source answered with an application level
      error message of its own (a GraphQL ``errors`` list, for
      example) that is not a plain not-found.
    """

    def __init__(self, source: str, kind: str, detail: str):
        self.source = source
        self.kind = kind
        self.detail = detail
        super().__init__(f"{source}: {kind}: {detail}")
