"""Shared async HTTP client with per-host rate limiting and error mapping.

NCBI allows 3 requests/s without an API key and 10/s with one; going over
gets you 429s and eventually an IP block. The limiter keeps every agent in
a run, running in parallel, under one shared per-host rate.
"""
from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import urlparse

import httpx

from ..core.errors import MalformedResponse, NotFound, PermanentError, RateLimited, ToolTimeout, UpstreamServerError

USER_AGENT = "Aegis/0.3 (agent reliability research; contact via GitHub umeshlinga/aegis)"


class HostLimiter:
    def __init__(self, rates: dict[str, float] | None = None, default_rps: float = 5.0):
        self.rates = rates or {}
        self.default = default_rps
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        rps = self.rates.get(host, self.default)
        # asyncio locks bind to one event loop; key by loop so a process-wide
        # limiter also works across separate asyncio.run() calls.
        lock = self._locks.setdefault((id(asyncio.get_running_loop()), host), asyncio.Lock())
        async with lock:
            now = time.monotonic()
            t = self._next.get(host, now)
            if t > now:
                await asyncio.sleep(t - now)
            self._next[host] = max(now, t) + 1.0 / rps


_SHARED_LIMITERS: dict[bool, HostLimiter] = {}


def shared_limiter(ncbi_has_key: bool) -> HostLimiter:
    """One limiter per process, shared by every run.

    The first version created a limiter per Http client, i.e. per run. That
    was fine for one run at a time and wrong for batches: 8 concurrent runs
    each "respecting" 2.8 req/s add up to ~22 req/s against NCBI, which gets
    429s and eventually an IP block. Found when adding batch mode (F7)."""
    if ncbi_has_key not in _SHARED_LIMITERS:
        _SHARED_LIMITERS[ncbi_has_key] = HostLimiter({"eutils.ncbi.nlm.nih.gov": 9.0 if ncbi_has_key else 2.8,
                                                      "rest.ensembl.org": 12.0, "gnomad.broadinstitute.org": 4.0})
    return _SHARED_LIMITERS[ncbi_has_key]


class Http:
    def __init__(self, ncbi_has_key: bool = False, timeout_s: float = 30.0, transport=None):
        self.client = httpx.AsyncClient(timeout=timeout_s, headers={"User-Agent": USER_AGENT},
                                        follow_redirects=True, transport=transport)
        self.limiter = shared_limiter(ncbi_has_key)

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        host = urlparse(url).hostname or ""
        await self.limiter.wait(host)
        try:
            r = await self.client.request(method, url, **kw)
        except httpx.TimeoutException as e:
            raise ToolTimeout(f"{host} timeout") from e
        except httpx.TransportError as e:
            raise UpstreamServerError(f"{host} transport error: {e}") from e
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            raise RateLimited(f"{host} 429", float(ra) if ra and ra.replace('.', '', 1).isdigit() else None)
        if r.status_code >= 500:
            raise UpstreamServerError(f"{host} {r.status_code}")
        if r.status_code == 404:
            raise NotFound(f"{host} 404 {url[:120]}")
        if r.status_code >= 400:
            raise PermanentError(f"{host} {r.status_code}: {r.text[:200]}")
        return r

    async def get_json(self, url: str, **kw):
        r = await self.request("GET", url, **kw)
        return _json(r)

    async def post_json(self, url: str, **kw):
        r = await self.request("POST", url, **kw)
        return _json(r)

    async def get_text(self, url: str, **kw) -> str:
        r = await self.request("GET", url, **kw)
        return r.text


def _json(r: httpx.Response):
    try:
        return r.json()
    except (json.JSONDecodeError, ValueError) as e:
        raise MalformedResponse(f"non-JSON response from {r.url}: {r.text[:120]!r}") from e
