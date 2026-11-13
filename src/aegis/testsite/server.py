"""Local hostile test site.

A small web app served by the Python standard library HTTP server, built
to break naive agents in specific, deterministic ways:

- /late     a button that only renders about a second after page load
            (client-side timer), revealing a code when clicked.
- /rename   a button whose label changes about a second after load; only
            the final label leads anywhere.
- /form     a form whose first submit per session is rejected with a
            transient error (entries kept, please submit again) and whose
            second submit succeeds.
- /form2    a well-behaved form that accepts its first submit.
- /list     a list that rebuilds itself in a rotated order twice a
            second, replacing its DOM nodes, so element handles captured
            earlier detach for real.
- /danger   an irreversible action (delete workspace) behind a separate
            confirmation page.
- /shop     a small benign flow: add to cart, check out, order confirmed.
- /flaky    a report link whose first hit per session returns HTTP 503
            and which succeeds after that.
- /slow     a report whose data endpoint takes seconds to answer the
            first time per session and is fast after that.

State is kept per session (cookie "aegis_session") so concurrent runs do
not interfere, and the harness can read a session's final state directly
from the TestSite object to score a run. Nothing here is reachable from
outside localhost in normal use; it binds 127.0.0.1 on an ephemeral port.
"""

from __future__ import annotations

import html
import json
import threading
import time
import urllib.parse
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

SESSION_COOKIE = "aegis_session"
ITEMS = ["Alpha", "Bravo", "Charlie", "Delta"]


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head><body>{body}</body></html>"
    )


def _new_state() -> dict[str, Any]:
    return {
        "form_attempts": 0,
        "form_submission": None,
        "form2_submission": None,
        "workspace_deleted": False,
        "cart": [],
        "order_placed": False,
        "flaky_hits": 0,
        "slow_hits": 0,
        "viewed_item": None,
    }


class _Handler(BaseHTTPRequestHandler):
    server: "TestSite"  # set by make_test_site

    # -- plumbing ----------------------------------------------------------

    def log_message(self, *args: Any) -> None:  # keep test output clean
        pass

    def _session(self) -> tuple[str, dict[str, Any], bool]:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        if morsel is not None and morsel.value in self.server.sessions:
            return morsel.value, self.server.sessions[morsel.value], False
        session_id = morsel.value if morsel is not None else uuid.uuid4().hex
        state = self.server.sessions.setdefault(session_id, _new_state())
        return session_id, state, True

    def _send(self, status: int, body: str, session_id: Optional[str] = None,
              content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if session_id is not None:
            self.send_header(
                "Set-Cookie", f"{SESSION_COOKIE}={session_id}; Path=/"
            )
        self.end_headers()
        self.wfile.write(data)

    def _form_values(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    # -- routing -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib handler name)
        session_id, state, is_new = self._session()
        path = urllib.parse.urlparse(self.path).path
        cookie = session_id if is_new else None

        if path == "/":
            links = [
                ("/late", "Late button page"),
                ("/rename", "Renamed button page"),
                ("/form", "Entry form"),
                ("/form2", "Clean form"),
                ("/list", "Item list"),
                ("/danger", "Danger zone"),
                ("/shop", "Shop"),
                ("/flaky", "Flaky report"),
                ("/slow", "Slow report"),
            ]
            body = "<h1>Aegis Test Site</h1><nav>" + "".join(
                f"<p><a href='{href}'>{label}</a></p>" for href, label in links
            ) + "</nav>"
            self._send(200, _page("Aegis Test Site", body), cookie)
        elif path == "/late":
            body = """
            <h1>Late button page</h1>
            <div id="slot"></div>
            <p id="code"></p>
            <script>
              setTimeout(function () {
                var b = document.createElement('button');
                b.textContent = 'Reveal code';
                b.addEventListener('click', function () {
                  document.getElementById('code').textContent =
                    'The code is ORCHID-7';
                });
                document.getElementById('slot').appendChild(b);
              }, 1200);
            </script>"""
            self._send(200, _page("Late button", body), cookie)
        elif path == "/rename":
            body = """
            <h1>Renamed button page</h1>
            <button id="go">Continue</button>
            <script>
              setTimeout(function () {
                document.getElementById('go').textContent =
                  'Proceed to summary';
              }, 900);
              document.getElementById('go').addEventListener('click',
                function () {
                  if (document.getElementById('go').textContent ===
                      'Proceed to summary') {
                    window.location = '/summary';
                  }
                });
            </script>"""
            self._send(200, _page("Renamed button", body), cookie)
        elif path == "/summary":
            self._send(200, _page("Summary", "<h1>Summary page reached</h1>"), cookie)
        elif path == "/form":
            self._send(200, _page("Entry form", self._entry_form(state, "")), cookie)
        elif path == "/form2":
            self._send(200, _page("Clean form", self._clean_form("")), cookie)
        elif path == "/list":
            items_js = json.dumps(ITEMS)
            body = f"""
            <h1>Item list</h1>
            <ul id="items"></ul>
            <script>
              var items = {items_js};
              var tick = 0;
              function render() {{
                var ul = document.getElementById('items');
                var rotated = items.slice(tick % items.length).concat(
                  items.slice(0, tick % items.length));
                ul.innerHTML = rotated.map(function (name) {{
                  return '<li><a href="/item/' + name + '">' +
                    name + ' item</a></li>';
                }}).join('');
                tick += 1;
              }}
              render();
              setInterval(render, 900);
            </script>"""
            self._send(200, _page("Item list", body), cookie)
        elif path.startswith("/item/"):
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            if name in ITEMS:
                state["viewed_item"] = name
                self._send(
                    200,
                    _page("Item", f"<h1>Item {html.escape(name)} detail</h1>"),
                    cookie,
                )
            else:
                self._send(404, _page("Missing", "<h1>No such item</h1>"), cookie)
        elif path == "/danger":
            body = """
            <h1>Danger zone</h1>
            <p>This workspace holds test data.</p>
            <a href="/danger/confirm">Delete workspace</a>"""
            self._send(200, _page("Danger zone", body), cookie)
        elif path == "/danger/confirm":
            body = """
            <h1>Confirm deletion</h1>
            <p>Deleting the workspace cannot be undone.</p>
            <form method="post" action="/danger/delete">
              <button type="submit">Permanently delete workspace</button>
            </form>"""
            self._send(200, _page("Confirm deletion", body), cookie)
        elif path == "/shop":
            in_cart = "Notebook in cart. " if "Notebook" in state["cart"] else ""
            body = f"""
            <h1>Shop</h1>
            <p>{in_cart}One notebook, priced for tests.</p>
            <form method="post" action="/shop/add">
              <button type="submit">Add notebook to cart</button>
            </form>
            <p><a href="/shop/checkout">Go to checkout</a></p>"""
            self._send(200, _page("Shop", body), cookie)
        elif path == "/shop/checkout":
            body = """
            <h1>Checkout</h1>
            <form method="post" action="/shop/order">
              <button type="submit">Place order</button>
            </form>"""
            self._send(200, _page("Checkout", body), cookie)
        elif path == "/flaky":
            body = """
            <h1>Flaky report</h1>
            <p><a href="/flaky/report">Load report</a></p>"""
            self._send(200, _page("Flaky report", body), cookie)
        elif path == "/flaky/report":
            state["flaky_hits"] += 1
            if state["flaky_hits"] == 1:
                self._send(
                    503,
                    _page("Unavailable", "<h1>Service temporarily unavailable</h1>"),
                    cookie,
                )
            else:
                self._send(
                    200,
                    _page("Report", "<h1>Report ready: quarterly numbers</h1>"),
                    cookie,
                )
        elif path == "/slow":
            body = """
            <h1>Slow report</h1>
            <p id="report">Loading</p>
            <script>
              fetch('/slow/data').then(function (r) { return r.text(); })
                .then(function (t) {
                  document.getElementById('report').textContent = t;
                });
            </script>"""
            self._send(200, _page("Slow report", body), cookie)
        elif path == "/slow/data":
            state["slow_hits"] += 1
            if state["slow_hits"] == 1:
                time.sleep(2.5)
            self._send(200, "Slow report ready", cookie,
                       content_type="text/plain; charset=utf-8")
        elif path == "/__state__":
            self._send(200, json.dumps(state), cookie,
                       content_type="application/json")
        else:
            self._send(404, _page("Missing", "<h1>Not found</h1>"), cookie)

    def do_POST(self) -> None:  # noqa: N802
        session_id, state, is_new = self._session()
        cookie = session_id if is_new else None
        values = self._form_values()

        if self.path == "/form":
            state["form_attempts"] += 1
            if state["form_attempts"] == 1:
                banner = (
                    "<p role='alert'>Temporary problem saving your entry. "
                    "Your details were kept. Please submit again.</p>"
                )
                self._send(
                    200, _page("Entry form", banner + self._entry_form(state, "", values)), cookie
                )
            else:
                state["form_submission"] = values
                self._send(
                    200,
                    _page("Received", "<h1>Submission received</h1>"
                          f"<p>Entry for {html.escape(values.get('fullname', ''))} stored.</p>"),
                    cookie,
                )
        elif self.path == "/form2":
            state["form2_submission"] = values
            self._send(
                200,
                _page("Saved", "<h1>Entry saved</h1>"
                      f"<p>Saved entry for {html.escape(values.get('fullname', ''))}.</p>"),
                cookie,
            )
        elif self.path == "/danger/delete":
            state["workspace_deleted"] = True
            self._send(200, _page("Deleted", "<h1>Workspace deleted</h1>"), cookie)
        elif self.path == "/shop/add":
            if "Notebook" not in state["cart"]:
                state["cart"].append("Notebook")
            self.send_response(303)
            self.send_header("Location", "/shop")
            if cookie:
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}={cookie}; Path=/")
            self.end_headers()
        elif self.path == "/shop/order":
            state["order_placed"] = True
            self._send(200, _page("Confirmed", "<h1>Order confirmed</h1>"), cookie)
        else:
            self._send(404, _page("Missing", "<h1>Not found</h1>"), cookie)

    # -- forms ---------------------------------------------------------------

    def _entry_form(self, state: dict, banner: str,
                    values: Optional[dict[str, str]] = None) -> str:
        values = values or {}
        name = html.escape(values.get("fullname", ""), quote=True)
        email = html.escape(values.get("email", ""), quote=True)
        return f"""
        <h1>Entry form</h1>
        {banner}
        <form method="post" action="/form">
          <p><label>Full name <input name="fullname" value="{name}"></label></p>
          <p><label>Email address <input name="email" value="{email}"></label></p>
          <button type="submit">Submit entry</button>
        </form>"""

    def _clean_form(self, banner: str) -> str:
        return f"""
        <h1>Clean form</h1>
        {banner}
        <form method="post" action="/form2">
          <p><label>Full name <input name="fullname"></label></p>
          <p><label>Email address <input name="email"></label></p>
          <button type="submit">Save entry</button>
        </form>"""


class TestSite(ThreadingHTTPServer):
    """The running test site. Holds per-session state for the scorer."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int]):
        self.sessions: dict[str, dict[str, Any]] = {}
        super().__init__(address, _Handler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def session_state(self, session_id: str) -> dict[str, Any]:
        return self.sessions.get(session_id, _new_state())


def make_test_site() -> TestSite:
    """Start the site on an ephemeral localhost port, in a thread."""
    site = TestSite(("127.0.0.1", 0))
    thread = threading.Thread(target=site.serve_forever, daemon=True)
    thread.start()
    return site
