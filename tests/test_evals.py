"""Eval harness tests: the shipped suite loads at full size, and a mini
suite executed end to end shows the ablation behaving as designed (a
task with a designed-in transient failure passes with recovery on and
fails with recovery off), with the results table computed from the runs.
"""

import json

from aegis.evals.execute import render_results, run_suite
from aegis.evals.suite import load_suite

SUITE_PATH = "evals/tasks.json"


def test_shipped_suite_loads_with_30_tasks():
    suite = load_suite(SUITE_PATH)
    assert suite.name == "aegis-suite-v2"
    assert len(suite.tasks) == 30
    kinds = {t.kind for t in suite.tasks}
    assert kinds == {"browser", "api"}
    browser = [t for t in suite.tasks if t.kind == "browser"]
    api = [t for t in suite.tasks if t.kind == "api"]
    assert len(browser) == 16 and len(api) == 14
    for task in suite.tasks:
        assert task.setup and "plan" in task.setup


def _mini_suite(tmp_path):
    data = {
        "suite": "aegis-mini-test",
        "tasks": [
            {
                "id": "m1-benign",
                "kind": "api",
                "text": "append a line",
                "setup": {
                    "plan": [
                        {"tool": "append_note",
                         "args": {"key": "inbox", "line": "mini"},
                         "expect_substring": "mini"}
                    ]
                },
                "scorer": {"run_status": "done",
                           "notes_contains": {"inbox": "mini"}},
            },
            {
                "id": "m2-flaky",
                "kind": "api",
                "text": "append despite one transient failure",
                "setup": {
                    "unreliable": {"append_note": 1},
                    "plan": [
                        {"tool": "append_note",
                         "args": {"key": "inbox", "line": "recovered"},
                         "expect_substring": "recovered"}
                    ]
                },
                "scorer": {"run_status": "done",
                           "notes_contains": {"inbox": "recovered"}},
            },
        ],
    }
    path = tmp_path / "mini.json"
    path.write_text(json.dumps(data))
    return load_suite(path)


def test_mini_suite_ablation(tmp_path):
    suite = _mini_suite(tmp_path)
    outcomes = run_suite(suite, tmp_path / "work")
    by = {(o.task_id, o.mode): o for o in outcomes}
    assert by[("m1-benign", "on")].passed
    assert by[("m1-benign", "off")].passed
    assert by[("m2-flaky", "on")].passed
    assert not by[("m2-flaky", "off")].passed
    assert by[("m2-flaky", "on")].recovery_events == 1

    report = render_results(suite, outcomes, "2026-11-13")
    assert "aegis-mini-test" in report
    assert "50.0%" in report  # recovery off: 1 of 2 tasks passed
    assert "100.0%" in report  # recovery on: 2 of 2
