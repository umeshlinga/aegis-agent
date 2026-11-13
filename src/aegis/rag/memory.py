"""Agent memory: reusable lessons learned from past runs.

After each run a reflection call turns the trace (failures, recoveries,
what the verifier rejected) into short, reusable lessons, e.g.
"PubMed: protein short form (R1699W) returns more hits than HGVS c. for BRCA1".

Feedback without regressions: a new lesson is stored as `candidate` and is
NOT shown to planners. It becomes `active` only when promoted, which the
eval harness does if a run with the lesson beats the same run without it
(or a human promotes it). Bad lessons cannot silently degrade the agent.
"""
from __future__ import annotations

import time
import uuid

from pydantic import BaseModel, Field

from ..llm.base import LLMRequest
from .index import HybridIndex


class Lesson(BaseModel):
    text: str = Field(description="One concrete, reusable instruction, <= 40 words")
    applies_to: str = Field(description="Tool, source or situation it applies to")


class Lessons(BaseModel):
    lessons: list[Lesson] = Field(default_factory=list, max_length=4)


class Memory:
    def __init__(self, index: HybridIndex):
        self.index = index

    def recall(self, query: str, workload: str, k: int = 4) -> list[dict]:
        res = self.index.search(query, k=k, filters={"status": "active", "workload": workload})
        return [{"id": h.doc_id, "text": h.text, **h.meta} for h in res.hits]

    def add(self, text: str, workload: str, source_run: str, status: str = "candidate") -> str:
        lid = "L" + uuid.uuid4().hex[:8]
        self.index.add(lid, text, {"status": status, "workload": workload, "source_run": source_run,
                                   "created": time.time()})
        return lid

    def set_status(self, lesson_id: str, status: str) -> bool:
        changed = False
        for i, (d, c, t, m) in enumerate(self.index.items):
            if d == lesson_id:
                m["status"] = status
                changed = True
                if self.index.db is not None:
                    import json

                    self.index.db.execute("UPDATE rag_docs SET meta_json=? WHERE collection=? AND doc_id=? AND chunk_id=?",
                                          (json.dumps(m), self.index.collection, d, c))
        if self.index.db is not None:
            self.index.db.commit()
        return changed

    def list(self, status: str | None = None) -> list[dict]:
        out = []
        for d, c, t, m in self.index.items:
            if status is None or m.get("status") == status:
                out.append({"id": d, "text": t, **m})
        return out

    async def reflect(self, router, workload: str, run_id: str, trace_summary: str) -> list[str]:
        req = LLMRequest(
            system=("You review an AI agent's run trace and write at most 4 short, reusable lessons that would make "
                    "future runs more reliable or cheaper. Only lessons supported by the trace. No generic advice."),
            messages=[{"role": "user", "content": trace_summary[:12000]}],
            tier="fast", purpose="reflect", max_tokens=600,
        )
        out, _ = await router.structured(req, Lessons)
        return [self.add(f"{l.text} (applies to: {l.applies_to})", workload, run_id) for l in out.lessons]
