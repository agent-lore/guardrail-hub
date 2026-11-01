"""Budget classification: [budgets] declarations vs measured metrics.

Mirrors the kit's ``budget_actual`` mapping (tests/guardrail/_metrics_toolkit.py
in each repo). An unknown budget key classifies as ``unknown`` (grey in the UI)
rather than raising — repos may grow new budget keys before the hub learns them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from guardrail_hub.models import BudgetLevel, BudgetStatus

# Public so tests can assert it mirrors the kit's BUDGET_ACTUALS key-for-key.
BUDGET_ACTUALS: dict[str, Callable[[Mapping[str, Any]], int]] = {
    "complexity_p90": lambda m: m["complexity"]["p90"],
    "component_cycles": lambda m: len(m["graph"]["component_cycles"]),
    "cross_component_edges": lambda m: m["graph"]["cross_component_edges"],
    "cross_component_module_edges": lambda m: m["graph"]["cross_component_module_edges"],
    "cross_module_private_refs": lambda m: m["seams"]["cross_module_private_refs"],
    "functions_over_10": lambda m: m["complexity"]["functions_over_10"],
    "functions_over_75_lines": lambda m: m["complexity"]["function_lengths"]["over_75"],
    "max_function_complexity": lambda m: m["complexity"]["max"],
    "max_module_lines": lambda m: m["size"]["max_module_lines"],
    "module_cycles": lambda m: m["graph"]["module_cycle_count"],
    "modules_over_800_lines": lambda m: len(m["size"]["modules_over_800"]),
    "tests_private_imports": lambda m: m["seams"]["tests_private_imports"],
    "tier_skipping_edges": lambda m: m["graph"]["tier_skipping_edges"],
}


def budget_actual(metrics: Mapping[str, Any], key: str) -> int | None:
    """Measured value for a budget key, or None when unknown/unreadable."""
    if (
        key in {"cross_module_private_refs", "tests_private_imports"}
        and metrics.get("measurement", {}).get("capabilities", {}).get("seams") == "unsupported"
    ):
        return None
    extractor = BUDGET_ACTUALS.get(key)
    if extractor is None:
        return None
    try:
        value = extractor(metrics)
    except (KeyError, TypeError, ValueError):
        return None
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _level(budget: int, actual: int | None) -> BudgetLevel:
    if actual is None:
        return "unknown"
    if actual > budget:
        return "breach"
    if actual == budget and budget != 0:
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
                policy=architecture.get("policies", {}).get(
                    key, "invariant" if key in {"component_cycles", "module_cycles"} else "review"
                ),
            )
        )
    return tuple(statuses)
