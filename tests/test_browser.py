"""Browser tool tests against the local hostile test site.

The driver is Playwright with a real Chromium. If Playwright or its
browser build is unavailable in the environment running the tests, the
whole module skips rather than failing: the tool raises a typed
BrowserToolError at session start, and the fixture turns that into a
skip. Everywhere else, these tests exercise the failure taxonomy for
real: first attempts fail with the designed kind, and the tool arm's
changed strategy (re-ground, re-read, extend the wait) succeeds on the
next attempt, which is exactly the contract the recovery engine relies on.
"""

import pytest

from aegis.testsite.server import make_test_site
from aegis.tools.browser import BrowserSession, BrowserToolError, make_browser_tools
from aegis.tools.registry import PermissionDenied, ToolRegistry


@pytest.fixture()
def site():
    site = make_test_site()
    yield site
    site.shutdown()


@pytest.fixture()
def session(site):
    session = BrowserSession(site.base_url, session_id="test-session")
    try:
        session.start()
    except BrowserToolError as exc:
        pytest.skip(f"browser unavailable in this environment: {exc}")
    yield session
    session.close()


def test_late_button_fails_first_then_regrounds(session):
    session.navigate("/late")
    with pytest.raises(BrowserToolError) as caught:
        session.click("button", "Reveal code")
    assert caught.value.kind == "element_not_found"
    text = session.click("button", "Reveal code",
                         expect_text="The code is ORCHID-7")
    assert "ORCHID-7" in text


def test_renamed_button_fails_first_then_regrounds(session):
    session.navigate("/rename")
    with pytest.raises(BrowserToolError) as caught:
        session.click("button", "Proceed to summary")
    assert caught.value.kind == "element_not_found"
    text = session.click("button", "Proceed to summary",
                         expect_text="Summary page reached")
    assert "Summary page reached" in text


def test_form_transient_rejection_is_verify_failed_then_succeeds(session, site):
    session.navigate("/form")
    session.fill("Full name", "Ada Lovelace")
    session.fill("Email address", "ada@example.test")
    with pytest.raises(BrowserToolError) as caught:
        session.click("button", "Submit entry",
                      expect_text="Submission received")
    assert caught.value.kind == "verify_failed"
    text = session.click("button", "Submit entry",
                         expect_text="Submission received")
    assert "Submission received" in text
    state = site.session_state("test-session")
    assert state["form_submission"]["fullname"] == "Ada Lovelace"


def test_stale_handle_then_reground_by_descriptor(session, site):
    session.navigate("/list")
    snapshot = session.snapshot()
    assert "Bravo item" in snapshot
    session.read()  # a fresh read: earlier handles are no longer trusted
    with pytest.raises(BrowserToolError) as caught:
        session.click_handle("h2", expect_text="Item Bravo detail")
    assert caught.value.kind == "stale_state"
    text = session.click_handle("h2", expect_text="Item Bravo detail")
    assert "Item Bravo detail" in text
    assert site.session_state("test-session")["viewed_item"] == "Bravo"


def test_flaky_navigation_is_tool_error_then_succeeds(session):
    with pytest.raises(BrowserToolError) as caught:
        session.navigate("/flaky/report")
    assert caught.value.kind == "tool_error"
    text = session.navigate("/flaky/report")
    assert "Report ready" in text


def test_slow_read_is_timeout_then_succeeds(session):
    session.navigate("/slow")
    with pytest.raises(BrowserToolError) as caught:
        session.read(wait_for_text="Slow report ready")
    assert caught.value.kind == "timeout"
    text = session.read(wait_for_text="Slow report ready")
    assert "Slow report ready" in text


def test_fill_verifies_value(session):
    session.navigate("/form2")
    assert session.fill("Full name", "Grace Hopper") == "filled 'Full name'"


def test_irreversible_tool_is_gated_by_registry(session, site):
    registry = ToolRegistry()
    for spec in make_browser_tools(session):
        registry.register(spec)
    with pytest.raises(PermissionDenied):
        registry.execute(
            "browser_confirm_irreversible",
            {"role": "button", "name": "Permanently delete workspace"},
        )
    assert site.session_state("test-session")["workspace_deleted"] is False
