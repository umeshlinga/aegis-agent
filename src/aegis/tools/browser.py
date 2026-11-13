"""Browser tool: the computer-use arm, driven by Playwright/Chromium.

Phase 2 ships the Playwright driver: a real Chromium rendering real
pages, including their JavaScript. (The DOM-level fallback driver was
considered and not needed; Playwright and its Chromium build are present
in this environment. The eval harness still labels its results with the
driver name so a run can never be mistaken for something it was not.)

Rules the tool enforces:

- Grounding is semantic: elements are found by ARIA role and accessible
  name (Playwright get_by_role), or by form label (get_by_label). Never
  by brittle CSS id or by coordinates.
- Every action is verified. A click must change the page (or reach the
  caller-declared expected text); a fill must read back the value it set.
  A failed check raises a typed failure instead of reporting success.
- Failures are typed and feed the recovery engine: element_not_found,
  stale_state, timeout, tool_error, verify_failed. The exception carries
  a .kind attribute, which is how the registry classifies it.
- Strategy changes live here, in the tool arm, as the recovery engine's
  strategy names describe: after an element_not_found, the next attempt
  at the same target re-grounds with an extended wait (pages render late;
  a wider, more patient search is a genuinely different strategy). After
  a stale_state, the next attempt at the same handle re-reads state and
  re-grounds from the handle's stored descriptor. After a timeout, the
  next read of the same text waits longer. With recovery disabled none of
  this matters: the first failure aborts the run, which is the ablation.

The irreversible-confirm tool is a separate ToolSpec, marked irreversible
in the registry, so the approval gate decides before this code runs.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .registry import PermissionScope, RunPolicy, ToolSpec

# Failure kinds, spelled exactly as the recovery taxonomy spells them.
ELEMENT_NOT_FOUND = "element_not_found"
STALE_STATE = "stale_state"
TIMEOUT = "timeout"
TOOL_ERROR = "tool_error"
VERIFY_FAILED = "verify_failed"

PROBE_MS = 300          # first grounding attempt: deliberately impatient
EXTENDED_MS = 5000      # re-grounding attempt: patient
READ_PROBE_MS = 600     # first wait for expected text
MAX_TEXT = 2000         # cap on page text returned to the planner


class BrowserToolError(Exception):
    """A typed browser failure. .kind is read by the tool registry and
    classified by the recovery engine."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class BrowserSession:
    """One browser context against one site, in one server-side session.

    A session is created per run, so runs never share page state. The
    server-side session id is fixed by cookie before the first navigation,
    which is what lets the eval harness read the final server state for
    scoring.
    """

    def __init__(self, base_url: str, session_id: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self._pw = None
        self._browser = None
        self._page = None
        self._generation = 0
        self._handles: dict[str, dict[str, Any]] = {}
        self._reground: set[tuple[str, str]] = set()
        self._stale_retry: set[str] = set()
        self._slow_texts: set[str] = set()
        self._last_nav_status: Optional[int] = None

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserToolError(
                TOOL_ERROR, f"playwright is not installed: {exc}"
            ) from exc
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
        except Exception as exc:
            raise BrowserToolError(
                TOOL_ERROR, f"chromium failed to launch: {exc}"
            ) from exc
        context = self._browser.new_context()
        if self.session_id:
            host = self.base_url.split("://", 1)[1].split(":", 1)[0]
            context.add_cookies(
                [{
                    "name": "aegis_session",
                    "value": self.session_id,
                    "domain": host,
                    "path": "/",
                }]
            )
        self._page = context.new_page()
        self._page.on("response", self._on_response)

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        finally:
            self._browser = None
            self._pw = None
            self._page = None

    def _on_response(self, response: Any) -> None:
        try:
            if response.request.is_navigation_request():
                self._last_nav_status = response.status
        except Exception:
            pass

    @property
    def page(self):
        self.start()
        return self._page

    # -- helpers ---------------------------------------------------------------

    def _resolve(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return self.base_url + (url if url.startswith("/") else "/" + url)

    def _visible_text(self) -> str:
        try:
            return self.page.inner_text("body")[:MAX_TEXT]
        except Exception:
            return ""

    def _check_nav_status(self) -> None:
        if self._last_nav_status is not None and self._last_nav_status >= 500:
            status = self._last_nav_status
            self._last_nav_status = None
            raise BrowserToolError(
                TOOL_ERROR, f"navigation returned HTTP {status}"
            )

    def _find(self, role: str, name: str):
        """Ground an element by role and accessible name.

        First attempt probes briefly; a retry at a target that already
        failed re-grounds with an extended wait (the changed strategy).
        """
        from playwright.sync_api import TimeoutError as PWTimeout

        key = (role, name)
        budget = EXTENDED_MS if key in self._reground else PROBE_MS
        locator = self.page.get_by_role(role, name=name, exact=True)
        try:
            locator.wait_for(state="visible", timeout=budget)
        except PWTimeout:
            self._reground.add(key)
            raise BrowserToolError(
                ELEMENT_NOT_FOUND,
                f"no element with role '{role}' and name '{name}' "
                f"within {budget} ms",
            )
        self._reground.discard(key)
        return locator

    def _act_and_verify(self, act, expect_text: Optional[str]) -> str:
        """Run an action, then verify: the navigation (if any) must not be
        a server error, and the page must show the expected text or have
        changed. This is the explicit verify-after-every-action step."""
        before = self.page.content()
        self._last_nav_status = None
        act()
        try:
            self.page.wait_for_load_state("load", timeout=EXTENDED_MS)
        except Exception:
            pass
        self._check_nav_status()
        if expect_text is not None:
            text = self._visible_text()
            if expect_text not in text:
                raise BrowserToolError(
                    VERIFY_FAILED,
                    f"expected text {expect_text!r} not on page after action",
                )
            return text
        if self.page.content() == before:
            raise BrowserToolError(
                VERIFY_FAILED, "action produced no change in page state"
            )
        return self._visible_text()

    # -- tool operations -----------------------------------------------------

    def navigate(self, url: str) -> str:
        from playwright.sync_api import TimeoutError as PWTimeout

        try:
            response = self.page.goto(
                self._resolve(url), timeout=EXTENDED_MS, wait_until="load"
            )
        except PWTimeout as exc:
            raise BrowserToolError(TIMEOUT, f"navigation timed out: {exc}") from exc
        if response is not None and response.status >= 500:
            raise BrowserToolError(
                TOOL_ERROR, f"navigation returned HTTP {response.status}"
            )
        self._generation += 1
        self._handles.clear()
        return self._visible_text()

    def read(self, wait_for_text: Optional[str] = None,
             timeout_ms: Optional[int] = None) -> str:
        """Read the current page. A read is a fresh look at state: handles
        captured before it are no longer trusted (generation bump). When
        wait_for_text is given, waits for it; a first wait is impatient,
        and after a timeout the next wait for the same text is extended
        (the changed strategy for slow pages)."""
        from playwright.sync_api import TimeoutError as PWTimeout

        self._generation += 1
        if wait_for_text is not None:
            if wait_for_text in self._slow_texts:
                # Changed strategy after a timeout: wait longer, whatever
                # budget the plan asked for the first attempt.
                budget = EXTENDED_MS
            else:
                budget = timeout_ms or READ_PROBE_MS
            try:
                self.page.get_by_text(wait_for_text, exact=False).first.wait_for(
                    state="visible", timeout=budget
                )
            except PWTimeout:
                self._slow_texts.add(wait_for_text)
                raise BrowserToolError(
                    TIMEOUT,
                    f"text {wait_for_text!r} not present within {budget} ms",
                )
            self._slow_texts.discard(wait_for_text)
        return self._visible_text()

    def snapshot(self) -> str:
        """Capture handles for the interactive elements on the page.

        Handles let a plan refer to a specific element across steps. The
        handle records its descriptor (role and name) and the page
        generation; if the page moves on, the handle is stale and only
        its descriptor can save the step, by re-grounding.
        """
        handles = self.page.query_selector_all(
            "button, a[href], input, select, textarea, "
            "[role='button'], [role='link']"
        )
        self._handles.clear()
        found: list[dict[str, str]] = []
        for i, element in enumerate(handles, start=1):
            descriptor = self._describe(element)
            handle_id = f"h{i}"
            self._handles[handle_id] = {
                "element": element,
                "descriptor": descriptor,
                "generation": self._generation,
            }
            found.append({"handle": handle_id, **descriptor})
        return json.dumps(found)

    @staticmethod
    def _describe(element) -> dict[str, str]:
        tag = element.evaluate("el => el.tagName.toLowerCase()")
        role = element.evaluate("el => el.getAttribute('role')") or ""
        if not role:
            if tag == "a":
                role = "link"
            elif tag == "button" or (
                tag == "input"
                and element.evaluate("el => el.type") in ("submit", "button")
            ):
                role = "button"
            elif tag in ("input", "textarea", "select"):
                role = "textbox"
            else:
                role = tag
        name = (
            element.evaluate("el => el.getAttribute('aria-label')")
            or element.inner_text()
            or element.evaluate("el => el.value || ''")
            or ""
        ).strip()
        return {"role": role, "name": name}

    def click(self, role: str, name: str,
              expect_text: Optional[str] = None) -> str:
        locator = self._find(role, name)
        return self._act_and_verify(lambda: locator.click(), expect_text)

    def click_handle(self, handle: str,
                     expect_text: Optional[str] = None) -> str:
        entry = self._handles.get(handle)
        if entry is None:
            raise BrowserToolError(
                ELEMENT_NOT_FOUND, f"unknown handle '{handle}'"
            )
        stale = entry["generation"] != self._generation
        if not stale:
            try:
                if not entry["element"].is_visible():
                    stale = True
            except Exception:
                stale = True
        if stale:
            if handle in self._stale_retry:
                # Changed strategy: re-read state, re-ground by descriptor.
                self._stale_retry.discard(handle)
                descriptor = entry["descriptor"]
                return self.click(
                    descriptor["role"], descriptor["name"], expect_text
                )
            self._stale_retry.add(handle)
            raise BrowserToolError(
                STALE_STATE,
                f"handle '{handle}' refers to page state that has moved on",
            )
        element = entry["element"]
        try:
            return self._act_and_verify(lambda: element.click(), expect_text)
        except BrowserToolError:
            raise
        except Exception as exc:
            if "not attached" in str(exc) or "detached" in str(exc):
                self._stale_retry.add(handle)
                raise BrowserToolError(
                    STALE_STATE, f"handle '{handle}' detached from the page"
                ) from exc
            raise BrowserToolError(TOOL_ERROR, f"click failed: {exc}") from exc

    def fill(self, label: str, value: str) -> str:
        from playwright.sync_api import TimeoutError as PWTimeout

        key = ("textbox", label)
        budget = EXTENDED_MS if key in self._reground else PROBE_MS
        locator = self.page.get_by_label(label, exact=True)
        try:
            locator.wait_for(state="visible", timeout=budget)
        except PWTimeout:
            self._reground.add(key)
            raise BrowserToolError(
                ELEMENT_NOT_FOUND, f"no field labelled '{label}'"
            )
        self._reground.discard(key)
        locator.fill(value)
        # Verify after the action: the field must hold what was set.
        actual = locator.input_value()
        if actual != value:
            raise BrowserToolError(
                VERIFY_FAILED,
                f"field '{label}' holds {actual!r}, expected {value!r}",
            )
        return f"filled '{label}'"

    def confirm_irreversible(self, role: str, name: str,
                             expect_text: Optional[str] = None) -> str:
        """The irreversible arm. Identical mechanics to click; what makes
        it different is the registry spec: irreversible, so the approval
        gate runs before this is ever called."""
        return self.click(role, name, expect_text)


def make_browser_tools(session: BrowserSession) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="browser_navigate",
            description="Open a page on the test site by path.",
            scope=PermissionScope.READ,
            handler=session.navigate,
            policy=RunPolicy(timeout_s=30.0),
        ),
        ToolSpec(
            name="browser_read",
            description="Read the visible text of the current page, "
                        "optionally waiting for given text.",
            scope=PermissionScope.READ,
            handler=session.read,
            policy=RunPolicy(timeout_s=30.0),
        ),
        ToolSpec(
            name="browser_snapshot",
            description="List interactive elements on the page as handles "
                        "with role and name descriptors.",
            scope=PermissionScope.READ,
            handler=session.snapshot,
            policy=RunPolicy(timeout_s=30.0),
        ),
        ToolSpec(
            name="browser_click",
            description="Click an element grounded by role and name, then "
                        "verify the page state.",
            scope=PermissionScope.ACT,
            handler=session.click,
            policy=RunPolicy(timeout_s=30.0),
        ),
        ToolSpec(
            name="browser_click_handle",
            description="Click an element captured earlier by snapshot "
                        "handle. Handles go stale when the page changes.",
            scope=PermissionScope.ACT,
            handler=session.click_handle,
            policy=RunPolicy(timeout_s=30.0),
        ),
        ToolSpec(
            name="browser_fill",
            description="Fill a form field found by its label, then verify "
                        "the value landed.",
            scope=PermissionScope.WRITE,
            handler=session.fill,
            policy=RunPolicy(timeout_s=30.0),
        ),
        ToolSpec(
            name="browser_confirm_irreversible",
            description="Click the confirmation control of an irreversible "
                        "action. Gated by human approval.",
            scope=PermissionScope.ACT,
            handler=session.confirm_irreversible,
            policy=RunPolicy(timeout_s=30.0),
            irreversible=True,
        ),
    ]
