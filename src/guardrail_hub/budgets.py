"""Budget classification: [budgets] declarations vs measured metrics.

Mirrors the kit's ``budget_actual`` mapping (tests/guardrail/_metrics_toolkit.py
in each repo). An unknown budget key classifies as ``unknown`` (grey in the UI)
rather than raising — repos may grow new budget keys before the hub learns them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from guardrail_hub.models import BudgetLevel, BudgetStatus

_ACTUALS: dict[str, Callable[[Mapping[str, Any]], int]] = {
    "component_cycles": lambda m: len(m["graph"]["component_cycles"]),
    "cross_component_edges": lambda m: m["graph"]["cross_component_edges"],
    "cross_component_module_edges": lambda m: m["graph"]["cross_component_module_edges"],
    "max_module_lines": lambda m: m["size"]["max_module_lines"],
    "module_cycles": lambda m: m["graph"]["module_cycle_count"],
    "modules_over_800_lines": lambda m: len(m["size"]["modules_over_800"]),
}


def budget_actual(metrics: Mapping[str, Any], key: str) -> int | None:
    """Measured value for a budget key, or None when unknown/unreadable."""
    extractor = _ACTUALS.get(key)
    if extractor is None:
        return None
    try:
        value = extractor(metrics)
    except (KeyError, TypeError):
        return None
    return int(value)


def _level(budget: int, actual: int | None) -> BudgetLevel:
    if actual is None:
        return "unknown"
    if actual > budget:
        return "breach"
    if actual == budget:
        return "tight"
    return "ok"


def budget_statuses(
    architecture: Mapping[str, Any], metrics: Mapping[str, Any] | None
) -> tuple[BudgetStatus, ...]:
    """Classify every [budgets] entry against the metrics snapshot (sorted by key)."""
    declared = architecture.get("budgets", {})
    if not isinstance(declared, Mapping):
        return ()
    statuses: list[BudgetStatus] = []
    for key in sorted(declared):
        raw = declared[key]
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue  # malformed entry; the repo's own guardrail flags it
        actual = budget_actual(metrics, key) if metrics is not None else None
        statuses.append(
            BudgetStatus(
                key=key,
                budget=raw,
                actual=actual,
                headroom=None if actual is None else raw - actual,
                level=_level(raw, actual),
            )
        )
    return tuple(statuses)
