"""Unit tests for budget classification."""

from __future__ import annotations

from tests.conftest import make_metrics

from guardrail_hub.budgets import budget_actual, budget_statuses

ARCH = {
    "budgets": {
        "cross_component_edges": 6,
        "component_cycles": 0,
        "max_module_lines": 200,
        "future_metric": 3,
    }
}


def test_actual_extraction() -> None:
    metrics = make_metrics()

    assert budget_actual(metrics, "cross_component_edges") == 5
    assert budget_actual(metrics, "component_cycles") == 0
    assert budget_actual(metrics, "modules_over_800_lines") == 0
    assert budget_actual(metrics, "max_module_lines") == 200
    assert budget_actual(metrics, "module_cycles") == 0
    assert budget_actual(metrics, "cross_module_private_refs") == 2
    assert budget_actual(metrics, "tests_private_imports") == 1


def test_unknown_key_is_none() -> None:
    assert budget_actual(make_metrics(), "future_metric") is None


def test_missing_section_is_none() -> None:
    assert budget_actual({}, "cross_component_edges") is None


def test_levels() -> None:
    statuses = {s.key: s for s in budget_statuses(ARCH, make_metrics())}

    assert statuses["cross_component_edges"].level == "ok"  # 5 < 6
    assert statuses["cross_component_edges"].headroom == 1
    assert statuses["component_cycles"].level == "tight"  # 0 == 0
    assert statuses["max_module_lines"].level == "tight"  # 200 == 200
    assert statuses["future_metric"].level == "unknown"
    assert statuses["future_metric"].actual is None


def test_breach() -> None:
    metrics = make_metrics(graph={"cross_component_edges": 9})

    statuses = {s.key: s for s in budget_statuses(ARCH, metrics)}

    assert statuses["cross_component_edges"].level == "breach"
    assert statuses["cross_component_edges"].headroom == -3


def test_no_metrics_all_unknown() -> None:
    statuses = budget_statuses(ARCH, None)

    assert statuses and all(s.level == "unknown" for s in statuses)


def test_no_budgets_section() -> None:
    assert budget_statuses({}, make_metrics()) == ()
