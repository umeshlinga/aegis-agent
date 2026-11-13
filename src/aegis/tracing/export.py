"""Turn failed or suspicious traces into regression eval cases.

Every production failure should become a test. This exports runs that
failed, degraded, or had evidence rejected, with their failure labels and
the input needed to replay them, to a JSONL the eval harness can load.
"""
from __future__ import annotations

import json
from pathlib import Path

from .taxonomy import label_run

INTERESTING = {"degraded_step", "unsupported_claim_rejected", "hallucinated_citation_rejected", "agent_loop",
               "browser_no_effect", "prompt_injection_blocked", "schema_violation", "crash_recovered"}


def export_failures(store, out_path: str, limit: int = 1000) -> int:
    n = 0
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "a") as f:
        for r in store.list_runs(limit=limit):
            labels = label_run(store.spans(r["run_id"]), store.events(r["run_id"]))
            if r["status"] == "failed" or INTERESTING & set(labels):
                run = store.get_run(r["run_id"])
                inp = {k: v for k, v in run["input"].items() if not k.startswith("_")}
                f.write(json.dumps({"case_id": f"reg-{r['run_id']}", "workload": r["workload"], **inp,
                                    "source_run": r["run_id"], "status": r["status"], "error": r["error"],
                                    "failure_labels": dict(labels), "split": "regression"}) + "\n")
                n += 1
    return n
