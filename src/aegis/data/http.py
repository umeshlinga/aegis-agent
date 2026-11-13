"""HTTP plumbing shared by the data clients, standard library only.

This mirrors the model adapters: one default transport built on
urllib, and an injectable transport callable so tests can replay
recorded responses and never touch the network. A transport takes a
URL, an optional request body (bytes, for POST), and headers, and
returns the response body as text. Every failure leaves here as a
DataSourceError with a kind, so the clients above this layer only have
to think about parsing, not about sockets.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from typing import Callable, Optional

from .cache import DiskCache
from .errors import DataSourceError

# (url, body_or_None, headers) -> response text
Transport = Callable[[str, Optional[bytes], dict[str, str]], str]

USER_AGENT = "aegis-agent/0.2 (genomic evidence research; python urllib)"


def urllib_transport(source: str, timeout: float = 30.0) -> Transport:
    """The production transport. Timeout is per request, in seconds."""

    def _send(url: str, body: Optional[bytes], headers: dict[str, str]) -> str:
        request = urllib.request.Request(
            url,
            data=body,
            headers={"User-Agent": USER_AGENT, **headers},
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise DataSourceError(
                source, "http_error", f"HTTP {exc.code} from {url}"
            ) from exc
        except socket.timeout as exc:
            raise DataSourceError(
                source, "timeout", f"no answer within {timeout} seconds"
            ) from exc
        except urllib.error.URLError as exc:
            # A URLError wraps the real cause. A timeout inside it is
            # still a timeout; everything else is a network failure.
            if isinstance(exc.reason, socket.timeout):
                raise DataSourceError(
                    source, "timeout", f"no answer within {timeout} seconds"
                ) from exc
            raise DataSourceError(
                source, "network_error", f"request failed: {exc.reason}"
            ) from exc
        except (UnicodeDecodeError, OSError) as exc:
            raise DataSourceError(
                source, "network_error", f"request failed: {exc}"
            ) from exc

    return _send


class FetchedText:
    """Response text plus its provenance: when it was fetched live,
    and whether this copy came from the cache."""

    def __init__(self, text: str, fetched_at: str, from_cache: bool):
        self.text = text
        self.fetched_at = fetched_at
        self.from_cache = from_cache


def fetch_text(
    source: str,
    request_key: str,
    url: str,
    body: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    cache: Optional[DiskCache] = None,
    transport: Optional[Transport] = None,
    timeout: float = 30.0,
) -> FetchedText:
    """Fetch one URL, through the cache when one is given.

    The cache is checked first under ``request_key`` (a stable string
    the caller builds from the source name and the query, so two
    different queries never collide). On a cache hit no network call
    happens and the original fetch timestamp is returned with the
    text. On a miss the live response is stored in the cache before
    it is returned, stamped with the moment it was fetched.
    """
    if cache is not None:
        cached = cache.lookup(request_key)
        if cached is not None:
            return FetchedText(cached.response_text, cached.fetched_at, True)

    send = transport or urllib_transport(source, timeout=timeout)
    text = send(url, body, headers or {})
    if not isinstance(text, str):
        raise DataSourceError(
            source, "parse_error", "transport returned a non-text response"
        )
    fetched_at = ""
    if cache is not None:
        fetched_at = cache.store(request_key, text)
    else:
        from .cache import utc_now_iso

        fetched_at = utc_now_iso()
    return FetchedText(text, fetched_at, False)
