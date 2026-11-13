"""Lightweight PII redaction for anything written to traces, outputs or drafts.

Regex rules cover the common US formats. For production on clinical text,
swap in Microsoft Presidio behind the same `redact` signature.
"""
from __future__ import annotations

import re

RULES = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("MRN", re.compile(r"\bMRN[:#\s]*\d{5,}\b", re.I)),
    ("DOB", re.compile(r"\b(?:DOB|date of birth)[:\s]*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.I)),
]


def redact(text: str, keep: set[str] | None = None) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    keep = keep or set()
    for label, pat in RULES:
        if label in keep:
            continue
        text, n = pat.subn(f"[{label}]", text)
        if n:
            counts[label] = n
    return text, counts
