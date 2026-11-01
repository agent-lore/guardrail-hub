"""Unit tests for the metrics-history miner and the dotted-key extractor."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_metrics, make_monorepo, make_repo

from guardrail_hub.history import extract, mine_history, series
from guardrail_hub.models import RepoEntry


def _entry(repo: Path) -> RepoEntry:
    return RepoEntry(name="fixture", path=repo, family="test")


def test_mine_history_ordered_oldest_first(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        snapshots=[
            make_metrics(graph={"cross_component_edges": 3}),
            make_metrics(graph={"cross_component_edges": 7}),
        ],
    )

    points = mine_history(_entry(repo), "main")

    assert [extract(p.metrics, "graph.cross_component_edges") for p in points] == [3, 7]
    assert points[0].date <= points[1].date


def test_mine_history_preserves_malformed_as_gap(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        snapshots=[
            make_metrics(graph={"cross_component_edges": 3}),
            "{not valid json",
            make_metrics(graph={"cross_component_edges": 9}),
        ],
    )

    points = mine_history(_entry(repo), "main")

    assert [extract(p.metrics, "graph.cross_component_edges") for p in points] == [3, None, 9]
    assert points[1].status == "invalid"


def test_mine_history_empty_when_never_committed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=["{}"])

    points = mine_history(_entry(repo), "main")

    assert len(points) == 1  # "{}" parses; a repo with no snapshot path gives ()


def test_extract_scalar_list_count_and_missing() -> None:
    metrics = make_metrics()

    assert extract(metrics, "graph.cross_component_edges") == 5
    assert extract(metrics, "graph.component_cycles.count") == 0
    assert extract(metrics, "size.modules_over_800.count") == 0
    assert extract(metrics, "mcp.tools") is None  # schema variance: adapter-only section
    assert extract(metrics, "graph.nope") is None


def test_extract_greedy_dotted_module_keys() -> None:
    metrics = make_metrics()

    assert extract(metrics, "modules.fixture.core.sloc") == 200
    assert extract(metrics, "modules.fixture.web.max_complexity") == 3
    assert extract(metrics, "modules.fixture.gone.sloc") is None


def test_extract_greedy_prefers_longest_joined_key() -> None:
    # the package module is a sibling key of its submodules in the modules
    # map — the longest joined run must win or the package entry shadows them
    metrics = {"modules": {"fixture": {"sloc": 10}, "fixture.core": {"sloc": 120}}}

    assert extract(metrics, "modules.fixture.core.sloc") == 120
    assert extract(metrics, "modules.fixture.sloc") == 10


def test_series_shape(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        snapshots=[
            make_metrics(graph={"cross_component_edges": 5}),
            make_metrics(graph={"cross_component_edges": 6}),
        ],
    )
    points = mine_history(_entry(repo), "main")

    payload = series(points, ["graph.cross_component_edges", "mcp.tools"])

    assert len(payload["dates"]) == len(payload["shas"]) == 2
    assert payload["series"]["graph.cross_component_edges"] == [5, 6]
    assert payload["series"]["mcp.tools"] == [None, None]


def test_mine_history_subdir_snapshot_path(tmp_path: Path) -> None:
    metrics_a = make_metrics(size={"total_sloc": 100})
    metrics_b = make_metrics(size={"total_sloc": 200})
    repo = make_monorepo(tmp_path, subdir="server", snapshots=[metrics_a, metrics_b])
    entry = RepoEntry(name="mono-server", path=repo, subdir="server")

    points = mine_history(entry, "main")

    assert [p.metrics["size"]["total_sloc"] for p in points] == [100, 200]


def test_history_preserves_same_second_commits_and_full_identity(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=[make_metrics(schema=1), make_metrics(schema=2)])
    points = mine_history(_entry(repo), "main")
    assert len(points) == 2
    assert all(len(p.sha) == 40 and "T" in p.timestamp for p in points)
    assert points[0].sha != points[1].sha
    payload = series(points, ["schema"])
    assert payload["series"]["schema"] == [1, 2]
    assert len(payload["timestamps"]) == 2


def test_definition_boundaries_and_unsupported_metrics_are_explicit(tmp_path: Path) -> None:
    a = make_metrics(
        measurement={"analyzer_version": "1", "capabilities": {"domain": "unsupported"}}
    )
    b = make_metrics(
        measurement={"analyzer_version": "2", "capabilities": {"domain": "unsupported"}}
    )
    repo = make_repo(tmp_path, snapshots=[a, b])
    payload = series(
        mine_history(_entry(repo), "main"), ["domain.models", "graph.component_cycles.count"]
    )
    assert payload["boundaries"] == [1]
    assert payload["series"]["domain.models"] == [None, None]
    assert payload["value_statuses"]["domain.models"] == ["unsupported", "unsupported"]
    assert payload["series"]["graph.component_cycles.count"] == [0, 0]


def test_invalid_nested_snapshot_and_non_numeric_series_are_gaps(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=[make_metrics(measurement=None), make_metrics()])
    points = mine_history(_entry(repo), "main")
    assert points[0].status == "invalid"
    payload = series(points, ["graph", "graph.component_cycles.count"])
    assert payload["series"]["graph"] == [None, None]
    assert payload["value_statuses"]["graph"] == ["invalid", "invalid"]
    assert payload["series"]["graph.component_cycles.count"] == [None, 0]
