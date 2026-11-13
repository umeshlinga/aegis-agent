"""Failure taxonomy: label what went wrong in a run from its spans and events.

Labels are assigned by rules over recorded data, not by a model, so the
counts are reproducible. Each run can carry several labels (a rate limit
that led to a timeout that led to a degraded literature step).
"""
from __future__ import annotations

from collections import Counter

ERROR_LABELS = {
    "ToolTimeout": "tool_timeout",
    "StepTimeout": "step_timeout",
    "TimeoutError": "step_timeout",
    "RateLimited": "rate_limited",
    "UpstreamServerError": "upstream_5xx",
    "MalformedResponse": "malformed_upstream",
    "CircuitOpen": "circuit_open",
    "SchemaViolation": "schema_violation",
    "LoopDetected": "agent_loop",
    "BudgetExceeded": "budget_exceeded",
    "PermissionDenied": "permission_denied",
    "InjectionBlocked": "prompt_injection_blocked",
    "DomainNotAllowed": "domain_blocked",
    "BrowserNoEffect": "browser_no_effect",
    "LocatorLost": "browser_locator_lost",
    "VerificationFailed": "verification_failed",
    "NotFound": "not_found",
}

EVENT_LABELS = {
    "steps_recovered_after_crash": "crash_recovered",
    "stale_result_discarded": "stale_result_discarded",
    "effect_deduplicated": "duplicate_effect_prevented",
    "approval_requested": "approval_gate",
    "step_degraded": "degraded_step",
    "claim_rejected": "unsupported_claim_rejected",
    "citation_rejected": "hallucinated_citation_rejected",
    "model_escalated": "model_escalated",
    "provider_fallback": "provider_fallback",
    "browser_recovered": "browser_recovered",
    "injection_detected": "prompt_injection_detected",
    "source_disagreement": "source_disagreement",
    "browser_action_refused": "browser_action_refused",
}


def label_run(spans: list[dict], events: list[dict]) -> Counter:
    labels: Counter = Counter()
    for s in spans:
        et = s.get("attrs", {}).get("error_type")
        if s.get("status") == "error" and et in ERROR_LABELS:
            labels[ERROR_LABELS[et]] += 1
        if s.get("attrs", {}).get("fault_injected"):
            labels["fault_injected"] += 1
    for e in events:
        if e["kind"] in EVENT_LABELS:
            labels[EVENT_LABELS[e["kind"]]] += 1
        if e["kind"] == "step_attempt_failed":
            et = e["data"].get("error_type")
            if et in ERROR_LABELS:
                labels[ERROR_LABELS[et]] += 1
    return labels


def summarize(store, run_ids: list[str]) -> Counter:
    total: Counter = Counter()
    for rid in run_ids:
        total.update(label_run(store.spans(rid), store.events(rid)))
    return total
