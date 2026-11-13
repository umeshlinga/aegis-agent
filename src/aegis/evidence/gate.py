"""The decline rule, in code.

A dossier earns an answer only when the evidence behind it is broad
enough to stand on: at least MIN_ITEMS verified items, drawn from at
least MIN_SOURCES different sources. Below either bar the verdict is
a decline, with the reason stated in numbers: how many verified items
there are, from how many sources, and what is missing.

The rule is deliberately simple and deliberately strict about one
thing: it runs on the verified items only, after the citation
checker, never on the raw dossier. Three items where one citation
failed checking are two items, and the gate counts two. And nothing
ever pads a thin dossier: the builder is not asked twice, items are
not duplicated, and a decline is a complete, honest result, not an
error state.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dossier import EvidenceItem

MIN_ITEMS = 3
MIN_SOURCES = 2


@dataclass
class GateVerdict:
    """Whether a set of verified items may support an answer."""

    answerable: bool
    item_count: int
    source_count: int
    sources: tuple[str, ...]  # distinct sources, sorted, for reports
    reason: str  # why this verdict, in plain words with the numbers


def evaluate_gate(verified_items: list[EvidenceItem]) -> GateVerdict:
    """Apply the decline rule to the verified items of one dossier."""
    sources = tuple(sorted({item.source for item in verified_items}))
    item_count = len(verified_items)
    source_count = len(sources)

    if item_count >= MIN_ITEMS and source_count >= MIN_SOURCES:
        reason = (
            f"{item_count} verified evidence items from "
            f"{source_count} different sources ({', '.join(sources)}), "
            f"which meets the bar of at least {MIN_ITEMS} items from "
            f"at least {MIN_SOURCES} sources."
        )
        return GateVerdict(
            answerable=True,
            item_count=item_count,
            source_count=source_count,
            sources=sources,
            reason=reason,
        )

    # Name each shortfall separately, so the reader learns exactly
    # what would have to change, not just that something is short.
    missing = []
    if item_count < MIN_ITEMS:
        missing.append(
            f"{MIN_ITEMS - item_count} more verified item"
            + ("s" if MIN_ITEMS - item_count != 1 else "")
        )
    if source_count < MIN_SOURCES:
        missing.append(
            f"evidence from {MIN_SOURCES - source_count} more source"
            + ("s" if MIN_SOURCES - source_count != 1 else "")
        )
    reason = (
        f"{item_count} verified evidence item"
        + ("s" if item_count != 1 else "")
        + f" from {source_count} source"
        + ("s" if source_count != 1 else "")
        + f". The bar is at least {MIN_ITEMS} items from at least "
        f"{MIN_SOURCES} different sources. Missing: "
        + " and ".join(missing)
        + "."
    )
    return GateVerdict(
        answerable=False,
        item_count=item_count,
        source_count=source_count,
        sources=sources,
        reason=reason,
    )
