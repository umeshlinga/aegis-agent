"""Calibrate the LLM judges against human labels before trusting them.

An LLM judge is a measuring instrument. Before its verdicts gate anything,
compare it with ~50 human-labelled examples and report agreement (Cohen's
kappa) and the confusion matrix, per judge and for the panel (both agree).

Label file (JSONL): {"claim": "...", "direction": "pathogenic", "quote": "...", "human_supports": true}
`aegis judge-calibrate --labels evals/data/judge_labels.jsonl`
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..llm.base import LLMRequest
from ..workloads.variant.workload import SupportVerdict


def cohen_kappa(a: list[bool], b: list[bool]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


async def calibrate(router, labels_path: str) -> dict:
    rows = [json.loads(l) for l in Path(labels_path).read_text().splitlines() if l.strip()]

    async def judge(tier: str, r: dict) -> bool:
        req = LLMRequest(system="You audit evidence for variant curation. Decide whether the quoted sentence, by itself, "
                                "supports the stated evidence direction for this exact variant.",
                         messages=[{"role": "user", "content": f"Claim: {r['claim']}, direction {r['direction']}\n"
                                                               f"Quote: \"{r['quote']}\""}],
                         tier=tier, purpose="support_judge", max_tokens=200)
        v, _ = await router.structured(req, SupportVerdict)
        return bool(v.supports and v.direction == r["direction"])

    human = [bool(r["human_supports"]) for r in rows]
    out = {"n": len(rows)}
    per = {}
    for tier in ("judge_a", "judge_b"):
        if not router.tiers.get(tier):
            continue
        preds = await asyncio.gather(*(judge(tier, r) for r in rows))
        per[tier] = preds
        tp = sum(p and h for p, h in zip(preds, human))
        fp = sum(p and not h for p, h in zip(preds, human))
        fn = sum((not p) and h for p, h in zip(preds, human))
        tn = sum((not p) and (not h) for p, h in zip(preds, human))
        out[tier] = {"route": [str(r) for r in router.tiers[tier]], "kappa": round(cohen_kappa(preds, human), 3),
                     "accuracy": round((tp + tn) / len(rows), 3), "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}}
    if len(per) == 2:
        panel = [a and b for a, b in zip(per["judge_a"], per["judge_b"])]
        out["panel_both_agree"] = {"kappa": round(cohen_kappa(panel, human), 3),
                                   "false_accepts": sum(p and not h for p, h in zip(panel, human))}
    return out
