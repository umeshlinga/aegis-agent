"""Span tracing, OpenTelemetry-shaped, persisted to the run store.

Every step attempt, LLM call, tool call and browser action is a span with
attributes (model, tokens, cost, tool, fault injected, error class). The
trace explorer and the failure taxonomy both read from here, and failed
traces can be exported as eval cases.

An OTLP exporter can be attached (AEGIS_OTLP_ENDPOINT) to ship the same
spans to Jaeger/Grafana Tempo; the SQLite copy is what the evals use.
"""
from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

_current: contextvars.ContextVar["Span | None"] = contextvars.ContextVar("aegis_span", default=None)


@dataclass
class Span:
    span_id: str
    run_id: str
    parent_id: str | None
    name: str
    kind: str
    start: float
    end: float | None = None
    status: str = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "span_id": self.span_id, "run_id": self.run_id, "parent_id": self.parent_id,
            "name": self.name, "kind": self.kind, "start": self.start, "end": self.end,
            "status": self.status, "attrs": self.attrs,
        }


_OTEL = None


def _otel_tracer():
    """Optional: mirror spans to any OTLP backend (Jaeger, Tempo, Honeycomb)
    when AEGIS_OTLP_ENDPOINT is set and the otel extra is installed."""
    global _OTEL
    if _OTEL is not None:
        return _OTEL or None
    import os

    ep = os.environ.get("AEGIS_OTLP_ENDPOINT")
    if not ep:
        _OTEL = False
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        tp = TracerProvider(resource=Resource.create({"service.name": "aegis"}))
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ep)))
        trace.set_tracer_provider(tp)
        _OTEL = trace.get_tracer("aegis")
    except Exception:
        _OTEL = False
    return _OTEL or None


class Tracer:
    def __init__(self, store, run_id: str):
        self.store = store
        self.run_id = run_id
        self.otel = _otel_tracer()

    @asynccontextmanager
    async def span(self, name: str, kind: str = "internal", attrs: dict | None = None):
        parent = _current.get()
        sp = Span(uuid.uuid4().hex[:16], self.run_id, parent.span_id if parent else None, name, kind,
                  time.time(), attrs=dict(attrs or {}))
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as e:
            sp.status = "error"
            sp.attrs["error_type"] = type(e).__name__
            sp.attrs["error"] = str(e)[:300]
            raise
        finally:
            sp.end = time.time()
            _current.reset(token)
            try:
                self.store.write_span(sp.to_row())
                if self.otel is not None:
                    o = self.otel.start_span(sp.name, start_time=int(sp.start * 1e9),
                                             attributes={"aegis.run_id": self.run_id, "aegis.kind": sp.kind,
                                                         **{f"aegis.{k}": str(v)[:200] for k, v in sp.attrs.items()}})
                    o.end(end_time=int(sp.end * 1e9))
            except Exception:
                pass  # tracing must never take a run down

    def event(self, name: str, attrs: dict | None = None) -> None:
        parent = _current.get()
        now = time.time()
        sp = Span(uuid.uuid4().hex[:16], self.run_id, parent.span_id if parent else None, name, "event",
                  now, now, attrs=dict(attrs or {}))
        self.store.write_span(sp.to_row())
