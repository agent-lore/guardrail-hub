"""Component hotspots: where mainline churn and complexity coincide.

Derived entirely from the already-mined metric time series (no extra I/O):
churn is the sum of |Δ lines| per component across consecutive mainline
snapshots, and the score weights that churn by the component's current worst
cyclomatic complexity — the classic behavioural-analysis heuristic that the
riskiest code is the code that is both complicated and constantly edited.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise
from typing import Any

from guardrail_hub.models import ComponentHotspot, MetricPoint


def _component_lines(metrics: Mapping[str, Any]) -> dict[str, int]:
    components = metrics.get("size", {}).get("components", {})
    if not isinstance(components, Mapping):
        return {}
    return {
        str(name): int(stats.get("lines", 0))
        for name, stats in components.items()
        if isinstance(stats, Mapping)
    }


def component_hotspots(points: tuple[MetricPoint, ...]) -> tuple[ComponentHotspot, ...]:
    """Hotspot rows for one repo, hottest first (empty when history is too short).

    A component only accrues churn between snapshots where it exists on both
    sides, so a rename or a newly mapped component does not spike the chart.
    """
    if len(points) < 2:
        return ()

    churn: dict[str, int] = {}
    for previous, current in pairwise(points):
        before, after = _component_lines(previous.metrics), _component_lines(current.metrics)
        for name in before.keys() & after.keys():
            churn[name] = churn.get(name, 0) + abs(after[name] - before[name])

    latest = points[-1].metrics
    lines = _component_lines(latest)
    complexity = latest.get("complexity", {}).get("components", {})
    if not isinstance(complexity, Mapping):
        complexity = {}

    rows = []
    for name in sorted(lines):
        c = complexity.get(name, {})
        c = c if isinstance(c, Mapping) else {}
        max_complexity = int(c.get("max_complexity", 0))
        component_churn = churn.get(name, 0)
        rows.append(
            ComponentHotspot(
                name=name,
                churn_lines=component_churn,
                lines=lines[name],
                max_complexity=max_complexity,
                functions_over_10=int(c.get("functions_over_10", 0)),
                # max(1, …) so complexity-free components (e.g. C++ instances
                # before complexity landed) still rank by raw churn.
                score=component_churn * max(1, max_complexity),
            )
        )
    return tuple(sorted(rows, key=lambda r: (-r.score, -r.churn_lines, r.name)))
