"""Comparison scoring and blocked configuration tests.

All fixture and stub based: no test here touches the network. The
scoring tests drive the pure scoring functions with hand built
outcomes. The runner tests bind a stub dossier builder that returns
hand built dossiers, so the Aegis configuration's path through the
real citation checker and the real gate is exercised without a
single live call. The blocked configuration tests pin the honesty
contract: the two LLM configurations are registered, named, and
reported as blocked on a live model API key, in the run object and
in the written results file.
"""

from aegis.comparison import (
    aggregate_scores,
    blocked_configurations,
    load_tasks,
    run_aegis_task,
    score_task,
    verdict_matches,
)
from aegis.comparison.configurations import BLOCKED_REASON
from aegis.comparison.runner import (
    ComparisonRun,
    ConfigurationResult,
    render_results,
    run_comparison,
)
from aegis.comparison.scoring import TaskOutcome
from aegis.comparison.tasks import ComparisonTask
from aegis.evidence import EvidenceDossier, EvidenceItem, EvidenceQuery


def task(expected="answerable", task_id="t1", gene="BRCA1"):
    return ComparisonTask(
        id=task_id,
        gene=gene,
        variant=None,
        question=f"What is known about {gene}?",
        task_type=expected,
        expected_outcome=expected,
        ground_truth="Fixture ground truth notes.",
        checked="2026-11-13",
    )


def outcome(verdict="answerable", total=10, verified=9, latency=1.5):
    return TaskOutcome(
        task_id="t1",
        verdict=verdict,
        items_total=total,
        items_verified=verified,
        latency_seconds=latency,
        live_items=total,
        cache_items=0,
    )


# Verdict matching: the gate's words against the task set's words.

def test_answerable_task_matches_answerable_verdict():
    assert verdict_matches(task("answerable"), "answerable") is True


def test_answerable_task_rejects_declined_verdict():
    assert verdict_matches(task("answerable"), "declined") is False


def test_decline_task_matches_declined_verdict():
    assert verdict_matches(task("decline"), "declined") is True


def test_decline_task_rejects_answerable_verdict():
    assert verdict_matches(task("decline"), "answerable") is False


# Scoring: success, citation correctness, edge cases.

def test_score_task_success_and_citation_fraction():
    score = score_task(task("answerable"), outcome(verified=9, total=10))
    assert score.success is True
    assert score.citation_correctness == 0.9


def test_score_task_failed_verdict_scores_failure():
    score = score_task(task("decline"), outcome(verdict="answerable"))
    assert score.success is False


def test_score_task_with_no_items_has_zero_citation_correctness():
    score = score_task(task("decline"), outcome(verdict="declined", total=0, verified=0))
    assert score.success is True
    assert score.citation_correctness == 0.0


def test_aggregate_scores_roll_up_mechanically():
    scores = [
        score_task(task("answerable", "t1"), outcome("answerable", 10, 10, 1.0)),
        score_task(task("answerable", "t2"), outcome("declined", 4, 4, 2.0)),
        score_task(task("decline", "t3"), outcome("declined", 3, 3, 3.0)),
        score_task(task("decline", "t4"), outcome("answerable", 8, 6, 4.0)),
    ]
    agg = aggregate_scores(scores, gate_violations=0)
    assert agg.tasks_run == 4
    assert agg.tasks_passed == 2
    assert agg.task_success_rate == 0.5
    assert agg.citations_total == 25
    assert agg.citations_verified == 23
    assert agg.decline_tasks == 2
    assert agg.declines_correct == 1
    assert agg.decline_correctness == 0.5
    assert agg.answerable_tasks == 2
    assert agg.wrong_declines == 1
    assert agg.gate_violations == 0
    assert agg.mean_latency_seconds == 2.5


# The Aegis runner path, with a stub dossier builder.

def evidence_item(source, record_id, url):
    return EvidenceItem(
        source=source,
        record_id=record_id,
        title=f"{source} record {record_id}",
        summary="Stub dossier item.",
        source_url=url,
        fetched_at="2026-11-13T20:00:00Z",
        from_cache=False,
    )


def stub_builder_rich(query, max_per_source=5, cache=None):
    items = [
        evidence_item("clinvar", "5042465", "https://www.ncbi.nlm.nih.gov/clinvar/variation/5042465/"),
        evidence_item("clinvar", "5042298", "https://www.ncbi.nlm.nih.gov/clinvar/variation/5042298/"),
        evidence_item("pubmed", "43023292", "https://pubmed.ncbi.nlm.nih.gov/43023292/"),
    ]
    dossier = EvidenceDossier(query=query, items=list(items))
    dossier.returned_keys = tuple(item.key for item in items)
    return dossier


def stub_builder_thin(query, max_per_source=5, cache=None):
    items = [
        evidence_item("pubmed", "38518681", "https://pubmed.ncbi.nlm.nih.gov/38518681/"),
    ]
    dossier = EvidenceDossier(query=query, items=list(items))
    dossier.returned_keys = tuple(item.key for item in items)
    return dossier


def test_run_aegis_task_answerable_on_rich_stub_dossier():
    result = run_aegis_task(task("answerable"), dossier_builder=stub_builder_rich)
    assert result.verdict == "answerable"
    assert result.items_total == 3
    assert result.items_verified == 3
    assert result.live_items == 3
    assert result.cache_items == 0
    assert result.latency_seconds >= 0


def test_run_aegis_task_declines_on_thin_stub_dossier():
    result = run_aegis_task(task("decline"), dossier_builder=stub_builder_thin)
    assert result.verdict == "declined"
    assert result.items_verified == 1
    assert "Missing" in result.gate_reason


# Blocked configurations: registered, named, reported.

def test_blocked_configurations_are_registered_without_runners():
    blocked = blocked_configurations()
    assert [c.name for c in blocked] == ["plain-llm", "llm-with-tools"]
    for configuration in blocked:
        assert configuration.blocked is True
        assert configuration.run_task is None
        assert configuration.blocked_reason == BLOCKED_REASON


def test_partial_run_flag_and_blocked_results():
    run = ComparisonRun(
        suite="aegis-comparison-v1",
        run_date="2026-11-13",
        results=[
            ConfigurationResult(configuration=c) for c in blocked_configurations()
        ],
    )
    assert run.partial is True
    assert all(result.blocked for result in run.results)


def test_rendered_results_report_partial_run_and_blocked_names():
    scores = [score_task(task("answerable"), outcome())]
    from aegis.comparison.configurations import aegis_configuration

    run = ComparisonRun(
        suite="aegis-comparison-v1",
        run_date="2026-11-13",
        results=[
            *[ConfigurationResult(configuration=c) for c in blocked_configurations()],
            ConfigurationResult(
                configuration=aegis_configuration(lambda t: outcome()),
                scores=scores,
                aggregate=aggregate_scores(scores, gate_violations=0),
            ),
        ],
    )
    text = render_results(run)
    assert "PARTIAL RUN" in text
    assert "NOT run" in text
    assert "No winner is claimed" in text
    assert "plain-llm" in text
    assert "llm-with-tools" in text
    assert BLOCKED_REASON in text
    assert "Approval gate violations | 0" in text
    assert "no irreversible tools" in text


# The real task set file loads and obeys its own design rules.

def test_real_task_set_loads_with_ground_truth():
    tasks = load_tasks("evals/comparison_tasks.json")
    assert len(tasks) >= 12
    ids = [t.id for t in tasks]
    assert len(set(ids)) == len(ids)
    for t in tasks:
        assert t.expected_outcome in ("answerable", "decline")
        assert t.ground_truth
        assert t.checked == "2026-11-13"
    declines = [t for t in tasks if t.expected_outcome == "decline"]
    assert len(declines) >= 3


def test_load_tasks_rejects_task_without_ground_truth(tmp_path):
    import json

    bad = {
        "suite": "bad",
        "tasks": [
            {
                "id": "x1",
                "gene": "BRCA1",
                "question": "What is known?",
                "expected_outcome": "answerable",
            }
        ],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    try:
        load_tasks(path)
    except ValueError as exc:
        assert "ground_truth" in str(exc)
    else:
        raise AssertionError("a task without ground truth must be rejected")
