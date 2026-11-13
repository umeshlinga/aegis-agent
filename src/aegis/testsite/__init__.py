"""The local hostile test site: a small web app whose pages are designed
to break naive agents (late-rendered controls, renamed controls, transient
form rejections, self-reordering lists, slow data, flaky responses, and an
irreversible action behind a confirmation step). Served by a stdlib HTTP
server; driven by the browser tool in tests and evals."""

from .server import TestSite, make_test_site

__all__ = ["TestSite", "make_test_site"]
