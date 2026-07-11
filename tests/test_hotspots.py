"""Unit tests for the hotspot derivation (pure function over MetricPoints)."""

from __future__ import annotations

import datetime

from tests.conftest import make_metrics

from guardrail_hub.hotspots import component_hotspots
from guardrail_hub.models import MetricPoint


def _point(sha: str, **overrides) -> MetricPoint:
    return MetricPoint(sha=sha, date=datetime.date(2026, 7, 11), metrics=make_metrics(**overrides))


def test_too_short_history_is_empty() -> None:
    assert component_hotspots(()) == ()
    assert component_hotspots((_point("a"),)) == ()


def test_churn_accumulates_absolute_line_deltas() -> None:
    points = (
        _point("a", size={"components": {"Core": {"lines": 300}, "Web": {"lines": 150}}}),
        _point("b", size={"components": {"Core": {"lines": 260}, "Web": {"lines": 150}}}),
        _point("c", size={"components": {"Core": {"lines": 320}, "Web": {"lines": 155}}}),
    )

    rows = {r.name: r for r in component_hotspots(points)}

    assert rows["Core"].churn_lines == 40 + 60
    assert rows["Web"].churn_lines == 5
    assert rows["Core"].lines == 320  # current size from the latest point


def test_score_weights_churn_by_current_max_complexity() -> None:
    points = (
        _point("a", size={"components": {"Core": {"lines": 100}, "Web": {"lines": 100}}}),
        _point(
            "b",
            size={"components": {"Core": {"lines": 150}, "Web": {"lines": 150}}},
            complexity={
                "components": {
                    "Core": {"max_complexity": 8, "functions_over_10": 0},
                    "Web": {"max_complexity": 2, "functions_over_10": 0},
                }
            },
        ),
    )

    rows = component_hotspots(points)

    assert rows[0].name == "Core" and rows[0].score == 50 * 8
    assert rows[1].name == "Web" and rows[1].score == 50 * 2


def test_zero_complexity_component_ranks_by_raw_churn() -> None:
    points = (
        _point("a", size={"components": {"Cpp": {"lines": 100}}}),
        _point(
            "b",
            size={"components": {"Cpp": {"lines": 170}}},
            complexity={"components": {"Cpp": {"max_complexity": 0, "functions_over_10": 0}}},
        ),
    )

    (row,) = component_hotspots(points)

    assert row.score == 70  # max(1, 0) keeps churn visible for complexity-free instances


def test_component_appearing_mid_history_does_not_spike() -> None:
    points = (
        _point("a", size={"components": {"Core": {"lines": 100}}}),
        _point("b", size={"components": {"Core": {"lines": 100}, "New": {"lines": 900}}}),
    )

    rows = {r.name: r for r in component_hotspots(points)}

    assert rows["New"].churn_lines == 0  # birth is not churn
    assert rows["New"].lines == 900
