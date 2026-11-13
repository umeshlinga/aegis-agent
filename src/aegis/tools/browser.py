"""Browser automation with effect verification and a recovery ladder.

Most browser-agent failures are not "the model picked the wrong button".
They are: the click landed but nothing happened yet (late JS), the element
re-rendered between observe and act, an overlay intercepted the click, or
the page navigated somewhere unexpected. So every action here is:

    observe -> act -> VERIFY the effect -> on failure, climb the ladder

Ladder (each rung is tried only if the one before failed verification):
    1. wait for network idle / DOM settle, re-verify
    2. re-observe and re-locate the element by role+text (LocatorLost)
    3. scroll into view, retry
    4. JS-dispatched click (bypasses overlays)
    5. vision fallback: screenshot -> vision model -> coordinate click
    6. reload page and report BrowserNoEffect to the agent loop

Code-enforced safety (not prompts): domain allowlist on every navigation,
and clicks on irreversible-looking controls (submit, pay, send, delete...)
are refused unless the caller explicitly allows them.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..core.errors import BrowserNoEffect, DomainNotAllowed, LocatorLost, PermissionDenied, ToolTimeout

RISKY = re.compile(r"\b(submit|send|pay|purchase|buy|checkout|place order|delete|remove|unsubscribe|"
                   r"subscribe|sign ?up|register|confirm|transfer|book now|apply now)\b", re.I)

OBSERVE_JS = r"""
(maxEls) => {
  document.querySelectorAll('[data-aegis-id]').forEach(e => e.removeAttribute('data-aegis-id'));
  const sel = 'a[href], button, input, select, textarea, [role=button], [role=link], [role=tab], [onclick], summary';
  const out = [];
  let id = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    id += 1;
    el.setAttribute('data-aegis-id', String(id));
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') ||
                  el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().replace(/\s+/g, ' ');
    out.push({id, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', type: el.type || '',
              text: text.slice(0, 80), href: el.getAttribute('href') || '', value: (el.value || '').slice(0, 80),
              x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
    if (out.length >= maxEls) break;
  }
  const mainEl = document.querySelector('main, article, [role=main]') || document.body;
  const body = mainEl ? mainEl.innerText : '';
  return {url: location.href, title: document.title, elements: out, text: body,
          n_nodes: document.getElementsByTagName('*').length};
}
"""


class Element(BaseModel):
    id: int
    tag: str
    role: str = ""
    type: str = ""
    text: str = ""
    href: str = ""
    value: str = ""
    x: int = 0
    y: int = 0


class Observation(BaseModel):
    url: str
    title: str
    elements: list[Element]
    text: str
    n_nodes: int = 0

    @property
    def fingerprint(self) -> str:
        h = hashlib.sha256(f"{self.url}|{self.n_nodes}|{self.text[:20000]}".encode()).hexdigest()
        return h[:16]

    def element(self, eid: int) -> Element | None:
        return next((e for e in self.elements if e.id == eid), None)

    def render(self, max_text: int = 3000, max_elements: int = 60) -> str:
        lines = [f"URL: {self.url}", f"TITLE: {self.title}", "INTERACTIVE ELEMENTS (id | tag | text | href):"]
        for e in self.elements[:max_elements]:
            lines.append(f"[{e.id}] {e.tag}{'/' + e.type if e.type else ''} | {e.text} | {e.href[:80]}")
        return "\n".join(lines)


class BrowserAction(BaseModel):
    kind: Literal["goto", "click", "type", "press", "scroll", "extract", "back", "done"]
    target_id: int | None = None
    url: str | None = None
    text: str | None = None
    expect: str | None = Field(None, description="Text you expect to appear after this action, if any")
    reason: str = ""


@dataclass
class ActionOutcome:
    ok: bool
    obs: Observation
    strategy: str = "direct"  # which ladder rung produced the verified effect
    extracted: str | None = None
    note: str = ""


@dataclass
class BrowserStats:
    actions: int = 0
    verified_first_try: int = 0
    recovered: int = 0
    failed: int = 0
    by_strategy: dict[str, int] = field(default_factory=dict)


VisionLocator = Callable[[bytes, str], Awaitable[tuple[int, int] | None]]


class BrowserSession:
    def __init__(self, allowed_domains: list[str], executable_path: str | None = None, headless: bool = True,
                 settle_ms: int = 600, nav_timeout_ms: int = 20000, allow_risky: bool = False,
                 vision_locator: VisionLocator | None = None, trace_dir: str | None = None, ladder: bool = True):
        self.allowed = [d.lower().lstrip(".") for d in allowed_domains]
        self.executable_path = executable_path
        self.headless = headless
        self.settle_ms = settle_ms
        self.nav_timeout_ms = nav_timeout_ms
        self.allow_risky = allow_risky
        self.vision_locator = vision_locator
        self.trace_dir = trace_dir
        self.ladder = ladder  # False = naive agent: one click, no recovery (baseline/ablation)
        self.stats = BrowserStats()
        self._pw = self._browser = self._ctx = self.page = None

    # ------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> "BrowserSession":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        kw: dict[str, Any] = {"headless": self.headless}
        if self.executable_path:
            kw["executable_path"] = self.executable_path
        self._browser = await self._pw.chromium.launch(**kw)
        self._ctx = await self._browser.new_context(viewport={"width": 1280, "height": 900})
        if self.trace_dir:
            await self._ctx.tracing.start(screenshots=True, snapshots=True)
        self.page = await self._ctx.new_page()
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.trace_dir and self._ctx:
                await self._ctx.tracing.stop(path=f"{self.trace_dir}/playwright-trace.zip")
        finally:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()

    # -------------------------------------------------------------- helpers
    def domain_ok(self, url: str) -> bool:
        if url.startswith(("about:", "data:")):
            return True
        host = (urlparse(url).hostname or "").lower()
        if host in ("127.0.0.1", "localhost") and any(d in ("127.0.0.1", "localhost") for d in self.allowed):
            return True
        return any(host == d or host.endswith("." + d) for d in self.allowed)

    async def settle(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=self.settle_ms * 4)
        except Exception:
            pass
        await asyncio.sleep(self.settle_ms / 1000)

    async def observe(self, max_elements: int = 120) -> Observation:
        # A page that is mid-navigation destroys the JS context under us
        # ("Execution context was destroyed"). Found in the first local run:
        # a delayed JS redirect fired while we were observing. Wait for the
        # new document and observe again instead of failing the step.
        last: Exception | None = None
        for _ in range(4):
            try:
                data = await self.page.evaluate(OBSERVE_JS, max_elements)
                return Observation(**data)
            except Exception as e:
                msg = str(e)
                if "Execution context was destroyed" not in msg and "navigat" not in msg.lower():
                    raise
                last = e
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=self.nav_timeout_ms)
                except Exception:
                    await asyncio.sleep(0.3)
        raise BrowserNoEffect(f"page kept navigating while observing: {last}")

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(type="png")

    # ---------------------------------------------------------------- goto
    async def goto(self, url: str) -> Observation:
        if not self.domain_ok(url):
            raise DomainNotAllowed(f"navigation to {url} blocked; allowed: {self.allowed}")
        try:
            resp = await self.page.goto(url, timeout=self.nav_timeout_ms, wait_until="domcontentloaded")
        except Exception as e:
            if "Timeout" in type(e).__name__ or "timeout" in str(e).lower():
                raise ToolTimeout(f"navigation timeout {url}") from e
            raise BrowserNoEffect(f"navigation failed {url}: {e}") from e
        await self.settle()
        if resp is not None and resp.status >= 400:
            raise BrowserNoEffect(f"{url} returned HTTP {resp.status}")
        if not self.domain_ok(self.page.url):
            await self.page.go_back()
            raise DomainNotAllowed(f"redirected off allowlist to {self.page.url}")
        return await self.observe()

    # ------------------------------------------------------------- actions
    async def act(self, action: BrowserAction, before: Observation) -> ActionOutcome:
        """Execute one action with verification and the recovery ladder."""
        self.stats.actions += 1
        if action.kind == "goto":
            obs = await self.goto(action.url or "")
            return self._ok(obs, "direct")
        if action.kind == "back":
            await self.page.go_back()
            await self.settle()
            return self._ok(await self.observe(), "direct")
        if action.kind == "scroll":
            await self.page.mouse.wheel(0, 900)
            await self.settle()
            return self._ok(await self.observe(), "direct")
        if action.kind == "extract":
            obs = await self.observe()
            out = self._ok(obs, "direct")
            out.extracted = obs.text
            return out
        if action.kind == "done":
            return ActionOutcome(True, before, "direct")
        if action.kind == "press":
            await self.page.keyboard.press(action.text or "Enter")
            await self.settle()
            obs = await self.observe()
            if obs.fingerprint == before.fingerprint:
                raise BrowserNoEffect("key press changed nothing")
            return self._ok(obs, "direct")

        el = before.element(action.target_id or -1)
        if el is None:
            raise LocatorLost(f"element {action.target_id} not in last observation")
        if action.kind == "click":
            self._risk_check(el)
            return await self._click_with_ladder(el, action, before)
        if action.kind == "type":
            return await self._type(el, action.text or "", before)
        raise BrowserNoEffect(f"unsupported action {action.kind}")

    def _risk_check(self, el: Element) -> None:
        if not self.allow_risky and (RISKY.search(el.text) or el.type == "submit"):
            raise PermissionDenied(f"refusing irreversible-looking control '{el.text}' (type={el.type})")

    def _ok(self, obs: Observation, strategy: str) -> ActionOutcome:
        if strategy == "direct":
            self.stats.verified_first_try += 1
        else:
            self.stats.recovered += 1
        self.stats.by_strategy[strategy] = self.stats.by_strategy.get(strategy, 0) + 1
        return ActionOutcome(True, obs, strategy)

    async def _verify(self, before: Observation, expect: str | None, wait_ms: int) -> Observation | None:
        """Effect happened if the expected text appeared, or (no expectation)
        the page fingerprint changed. Polls until wait_ms."""
        deadline = asyncio.get_running_loop().time() + wait_ms / 1000
        while True:
            obs = await self.observe()
            if not self.domain_ok(obs.url):
                await self.page.go_back()
                raise DomainNotAllowed(f"click navigated off allowlist to {obs.url}")
            if expect:
                if expect.lower() in obs.text.lower() or expect.lower() in obs.title.lower():
                    return obs
            elif obs.fingerprint != before.fingerprint:
                return obs
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.15)

    def _locator(self, eid: int):
        return self.page.locator(f'[data-aegis-id="{eid}"]')

    async def _relocate(self, el: Element) -> Element | None:
        """Find the 'same' element after a re-render by tag + text (+ href)."""
        obs = await self.observe()
        same = [e for e in obs.elements if e.tag == el.tag and e.text == el.text and (e.href == el.href or not el.href)]
        return same[0] if same else None

    async def _click_with_ladder(self, el: Element, action: BrowserAction, before: Observation) -> ActionOutcome:
        expect = action.expect
        # rung 0: direct click
        try:
            await self._locator(el.id).click(timeout=3000)
        except Exception:
            pass  # fall through to verification; ladder handles it
        obs = await self._verify(before, expect, wait_ms=1500)
        if obs:
            return self._ok(obs, "direct")
        if not self.ladder:
            self.stats.failed += 1
            raise BrowserNoEffect(f"click on [{el.id}] '{el.text}' had no verified effect (no recovery ladder)")
        # rung 1: wait longer for late JS
        await self.settle()
        obs = await self._verify(before, expect, wait_ms=2500)
        if obs:
            return self._ok(obs, "wait_settle")
        # rung 2: re-locate after re-render, then click again
        moved = await self._relocate(el)
        if moved is not None:
            try:
                await self._locator(moved.id).click(timeout=3000)
            except Exception:
                pass
            obs = await self._verify(before, expect, wait_ms=2000)
            if obs:
                return self._ok(obs, "relocate")
            el = moved
        # rung 3: scroll into view and retry
        try:
            await self._locator(el.id).scroll_into_view_if_needed(timeout=2000)
            await self._locator(el.id).click(timeout=3000, force=True)
        except Exception:
            pass
        obs = await self._verify(before, expect, wait_ms=2000)
        if obs:
            return self._ok(obs, "scroll_force")
        # rung 4: JS click (bypasses overlays that intercept pointer events)
        try:
            await self._locator(el.id).evaluate("e => e.click()")
        except Exception:
            pass
        obs = await self._verify(before, expect, wait_ms=2000)
        if obs:
            return self._ok(obs, "js_click")
        # rung 5: vision fallback - ask a vision model where the target is
        if self.vision_locator is not None:
            png = await self.screenshot()
            xy = await self.vision_locator(png, f"{el.tag} labelled '{el.text}'" + (f" ({action.reason})" if action.reason else ""))
            if xy:
                await self.page.mouse.click(*xy)
                obs = await self._verify(before, expect, wait_ms=2500)
                if obs:
                    return self._ok(obs, "vision_click")
        self.stats.failed += 1
        raise BrowserNoEffect(f"click on [{el.id}] '{el.text}' had no verified effect after full ladder")

    async def _type(self, el: Element, text: str, before: Observation) -> ActionOutcome:
        loc = self._locator(el.id)
        try:
            await loc.fill(text, timeout=3000)
        except Exception as e:
            moved = await self._relocate(el)
            if moved is None:
                raise LocatorLost(f"input '{el.text}' disappeared") from e
            loc = self._locator(moved.id)
            await loc.fill(text, timeout=3000)
            strategy = "relocate"
        else:
            strategy = "direct"
        value = await loc.input_value()
        if value != text:
            raise BrowserNoEffect(f"typed '{text}' but field holds '{value}'")
        return self._ok(await self.observe(), strategy)


def b64png(png: bytes) -> str:
    return base64.b64encode(png).decode()
