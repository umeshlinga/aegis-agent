"""Per-run service container and the runtime factory.

`Runtime` wires one Store + Engine + per-run Services. Modes:

* live     - real LLM providers and real upstream APIs (needs keys)
* record   - live, and every LLM response is saved for replay
* replay   - LLM responses from a recording, upstream APIs live or fixture
* offline  - OfflineProvider + fixture tools; measures the runtime only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .core.engine import Engine
from .core.faults import FaultInjector
from .core.resilience import Budget
from .core.store import Store
from .llm.base import PricingTable
from .llm.providers import OfflineProvider, RecordingProvider, ReplayProvider
from .llm.router import Router
from .rag.index import HybridIndex, make_embedder, make_reranker
from .rag.memory import Memory
from .tools.registry import ToolExecutor
from .tracing.tracer import Tracer


@dataclass
class RunOptions:
    mode: str = "live"  # live | record | replay | offline
    faults: Any = "none"  # profile name or FaultProfile
    seed: int = 0
    approval_mode: str = "human"  # human | auto_approve | auto_reject
    system: str = "aegis"  # aegis | baseline | ablation names (see evals)
    recording: str = "data/recordings/llm.jsonl"
    allow_browser: bool = True
    eval_mask_clinvar: bool = False  # hide ClinVar from the classifier when it is the ground truth
    max_cost_usd: float | None = None
    features: dict[str, bool] = field(default_factory=dict)  # ablation switches

    def feature(self, name: str, default: bool = True) -> bool:
        return self.features.get(name, default)


@dataclass
class Services:
    run_id: str
    settings: Settings
    options: RunOptions
    store: Store
    tracer: Tracer
    budget: Budget
    faults: FaultInjector
    router: Router
    tools: ToolExecutor
    memory: Memory | None
    extras: dict[str, Any] = field(default_factory=dict)

    def usage_snapshot(self) -> dict:
        """Totals for the whole run, computed from persisted spans, so a run
        that crashed and resumed in a new process still reports everything it
        spent (in-memory counters would only see the second process)."""
        spans = self.store.spans(self.run_id)
        llm = [s for s in spans if s["kind"] == "llm" and s["status"] == "ok"]
        tools = [s for s in spans if s["kind"] == "tool"]
        events = self.store.events(self.run_id)
        return {
            "cost_usd": round(sum(s["attrs"].get("cost_usd", 0.0) for s in llm), 6),
            "llm_calls": len(llm),
            "input_tokens": sum(s["attrs"].get("in_tok", 0) for s in llm),
            "output_tokens": sum(s["attrs"].get("out_tok", 0) for s in llm),
            "unpriced_llm_calls": sum(1 for s in llm if not s["attrs"].get("priced", True)),
            "tool_calls": len(tools),
            "tool_retries": sum(1 for e in events if e["kind"] == "tool_retry"),
            "models": sorted({s["attrs"].get("route", "") for s in llm}),
            "elapsed_s": round((max((s["end"] or s["start"]) for s in spans) - min(s["start"] for s in spans)), 3)
            if spans else 0.0,
            "faults": self.faults.summary(),
            "router_this_process": vars(self.router.stats),
        }


class Runtime:
    def __init__(self, settings: Settings | None = None, options: RunOptions | None = None,
                 store: Store | None = None, offline_provider: OfflineProvider | None = None):
        self.settings = settings or Settings()
        self.options = options or RunOptions()
        self.store = store or Store(self.settings.db_path)
        self.offline_provider = offline_provider
        self._memory: Memory | None = None
        self._providers = None
        from .workloads import registry  # late import: workloads import services

        self.workloads = registry
        self.engine = Engine(self.store, {k: w.build for k, w in registry.WORKLOADS.items()}, self.make_services)

    # ------------------------------------------------------------ providers
    def providers(self) -> dict:
        if self._providers is not None:
            return self._providers
        o = self.options
        if o.mode == "offline":
            prov = self.offline_provider or OfflineProvider()
            self.workloads.register_offline_handlers(prov)
            self._providers = {"offline": prov}
        elif o.mode == "replay":
            self._providers = {n: ReplayProvider(n, o.recording) for n in ("anthropic", "openai", "local")}
        else:
            live = self.settings.providers()
            if not live:
                raise RuntimeError("No LLM provider configured. Set ANTHROPIC_API_KEY (and optionally OPENAI_API_KEY / "
                                   "LOCAL_LLM_BASE_URL) in .env, or use --mode offline.")
            if o.mode == "record":
                live = {n: RecordingProvider(p, o.recording) for n, p in live.items()}
            self._providers = live
        return self._providers

    def tiers(self):
        if self.options.mode == "offline":
            from .llm.base import Route

            return {t: [Route("offline", f"offline-{t}")] for t in ("fast", "balanced", "strong", "judge_a", "judge_b")}
        return self.settings.tiers()

    def memory(self) -> Memory:
        if self._memory is None:
            db = None if self.store.path == ":memory:" else str(Path(self.store.path).with_suffix(".memory.db"))
            emb = make_embedder("hashing" if self.options.mode == "offline" else self.settings.embedder,
                                self.settings.openai_key)
            self._memory = Memory(HybridIndex(emb, None, db_path=db, collection="memory"))
        return self._memory

    def make_retriever(self) -> HybridIndex:
        if self.options.mode == "offline":
            return HybridIndex(make_embedder("hashing"), None)
        return HybridIndex(make_embedder(self.settings.embedder, self.settings.openai_key),
                           make_reranker(self.settings.reranker))

    # --------------------------------------------------------------- per run
    def make_services(self, run_id: str, run_input: dict) -> Services:
        o = self.options
        tracer = Tracer(self.store, run_id)
        budget = Budget(max_cost_usd=o.max_cost_usd or self.settings.max_cost_usd)
        faults = FaultInjector(o.faults, seed=o.seed, run_id=run_id)
        router = Router(self.providers(), self.tiers(), budget, tracer, self.store, run_id,
                        PricingTable.from_json(self.settings.pricing_json))
        tools = ToolExecutor(self.store, run_id, tracer, faults, budget, approval_mode=o.approval_mode,
                             retries_enabled=o.feature("retries"))
        svc = Services(run_id, self.settings, o, self.store, tracer, budget, faults, router, tools,
                       self.memory() if o.feature("memory") else None)
        tools.services = svc
        svc.extras["make_retriever"] = self.make_retriever
        self.workloads.WORKLOADS[self.store.get_run(run_id)["workload"]].register_tools(svc)
        return svc

    # --------------------------------------------------------------- facade
    async def run(self, workload: str, run_input: dict, run_id: str | None = None):
        run_input = {**run_input, "_features": dict(self.options.features)}
        rid = self.store.create_run(workload, run_input, run_id)
        await self.engine.execute(rid)
        return rid

    async def resume(self, run_id: str):
        return await self.engine.resume(run_id)
