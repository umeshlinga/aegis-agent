"""The comparison harness: the Aegis evidence configuration, checked.

This package runs the market comparison defined in COMPARISON.md.
Its parts:

1. ``tasks`` loads the real task set (evals/comparison_tasks.json):
   real genes and variants, ground truth from the source records,
   expected outcomes set before any run.
2. ``configurations`` registers the three framework configurations.
   The two LLM configurations are present but blocked on a live
   model API key; the Aegis evidence configuration is runnable now.
3. ``runner`` executes the runnable configurations over the tasks
   on live data with the data layer cache, and writes the results
   file. A run with blocked configurations is a partial run and the
   results file says so, with no winner claimed.
4. ``scoring`` computes the metrics COMPARISON.md defines that
   apply without a model: task success, citation correctness,
   decline correctness, gate violations, and latency.

No LLM is involved in the runnable configuration, and the fake
adapter is never used to stand in for one. That is the honesty rule
the whole comparison stands on.
"""

from .configurations import (
    Configuration,
    aegis_configuration,
    blocked_configurations,
    llm_with_tools_configuration,
    plain_llm_configuration,
)
from .runner import ComparisonRun, ConfigurationResult, run_aegis_task, run_comparison, write_results
from .scoring import AggregateScores, TaskOutcome, TaskScore, aggregate_scores, score_task, verdict_matches
from .tasks import ComparisonTask, load_tasks

__all__ = [
    "AggregateScores",
    "ComparisonRun",
    "ComparisonTask",
    "Configuration",
    "ConfigurationResult",
    "TaskOutcome",
    "TaskScore",
    "aegis_configuration",
    "aggregate_scores",
    "blocked_configurations",
    "llm_with_tools_configuration",
    "load_tasks",
    "plain_llm_configuration",
    "run_aegis_task",
    "run_comparison",
    "score_task",
    "verdict_matches",
    "write_results",
]
