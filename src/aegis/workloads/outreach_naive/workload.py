"""Baseline arm for outreach: how most AI outbound works today.

Grab the company homepage, give its text to a strong model, ask for a
personalised opener. No retrieval across the site, no fact verification, no
citation rule. The comparison then checks every claim this email makes
against everything Aegis captured from the same site.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from ...core.engine import Step, StepContext, Workflow
from ...core.resilience import RetryPolicy
from ...guardrails import injection
from ...llm.base import LLMRequest
from ...tools.browser import BrowserSession


class NaiveEmail(BaseModel):
    subject: str
    body: str


async def step_fetch(ctx: StepContext) -> dict:
    svc = ctx.services
    ri = ctx.run_input
    domain = ri.get("domain") or ""
    async with BrowserSession([domain], svc.settings.chrome_path, svc.settings.headless) as b:
        obs = await b.goto(ri["start_url"])
    return {"url": obs.url, "text": obs.text[:12000]}


async def step_write(ctx: StepContext) -> dict:
    svc = ctx.services
    page = ctx.outputs["fetch"]
    sender = ctx.run_input.get("sender") or {}
    req = LLMRequest(system=("You are a top-performing SDR. Write a highly personalised cold email opener (3-5 sentences) "
                             "that mentions specific, impressive details about the prospect company."),
                     messages=[{"role": "user", "content": json.dumps({"company_homepage": page["text"][:8000],
                                                                       "url": page["url"], "sender": sender})}],
                     tier="balanced", purpose="naive_opener", max_tokens=500)
    email, resp = await svc.router.structured(req, NaiveEmail)
    return {**email.model_dump(), "model": resp.model}


async def step_final(ctx: StepContext) -> dict:
    return {**ctx.outputs["write"], "domain": ctx.run_input.get("domain")}


def build(run_input: dict) -> Workflow:
    rp = RetryPolicy(max_attempts=3, base_delay=0.5)
    return Workflow("outreach_naive", [
        Step("fetch", step_fetch, retry=rp, timeout_s=120),
        Step("write", step_write, ["fetch"], retry=rp),
        Step("final", step_final, ["write"], retry=rp),
    ], result_step="final")


def register_tools(svc) -> None:
    return None
