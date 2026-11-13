"""The configurations a comparison run can execute.

COMPARISON.md defines three configurations: a single plain LLM
call, an LLM with tools but no recovery and no gates, and full
Aegis. A configuration is a name plus a ``run_task`` callable that
takes a task and returns its recorded outcome.

The two LLM configurations are registered here as present but
blocked. They carry no run_task at all, only the reason they cannot
run: they need a live model API key, and this environment does not
have one. The runner reports blocked configurations in the results
file by name, with that reason, instead of leaving them silently
absent, and nothing in this repo will ever fill one of them in with
the scripted fake adapter and call the output a comparison. When a
real key arrives, a real run_task is registered here and the same
harness runs all three.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .scoring import TaskOutcome
from .tasks import ComparisonTask

# A run_task callable: one task in, its recorded outcome out. The
# callable owns its own clients and cache; the harness only scores
# what it returns.
RunTask = Callable[[ComparisonTask], TaskOutcome]

BLOCKED_REASON = "blocked: needs a live model API key"


@dataclass
class Configuration:
    """One way of running the comparison tasks."""

    name: str
    title: str
    description: str
    run_task: Optional[RunTask] = None
    blocked_reason: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.run_task is None


def plain_llm_configuration() -> Configuration:
    """Configuration (i): a single plain LLM call, no tools."""
    return Configuration(
        name="plain-llm",
        title="Single plain LLM call, no tools",
        description=(
            "One call, one prompt, the question in and an answer out. "
            "No retrieval, no verification, citations from memory."
        ),
        blocked_reason=BLOCKED_REASON,
    )


def llm_with_tools_configuration() -> Configuration:
    """Configuration (ii): an LLM with tools, no recovery, no gates."""
    return Configuration(
        name="llm-with-tools",
        title="LLM with tools, no recovery and no gates",
        description=(
            "The same model with the same real data tools, in a plain "
            "loop. The first failure ends the task. No permission "
            "gates and no citation checking before the answer."
        ),
        blocked_reason=BLOCKED_REASON,
    )


def aegis_configuration(run_task: RunTask) -> Configuration:
    """Configuration (iii): full Aegis, the evidence configuration."""
    return Configuration(
        name="full-aegis",
        title="Full Aegis (evidence configuration)",
        description=(
            "The evidence core end to end: live dossier over ClinVar, "
            "PubMed, and gnomAD, citation checking against the records "
            "the clients returned, the decline gate on verified items "
            "only, and the checked report. No model is in this loop."
        ),
        run_task=run_task,
    )


def blocked_configurations() -> list[Configuration]:
    """The configurations that cannot run yet, in framework order."""
    return [plain_llm_configuration(), llm_with_tools_configuration()]
