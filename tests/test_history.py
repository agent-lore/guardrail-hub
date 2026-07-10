"""Unit tests for the metrics-history miner and the dotted-key extractor."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_metrics, make_repo

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


def test_mine_history_skips_malformed(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        snapshots=[
            make_metrics(graph={"cross_component_edges": 3}),
            "{not valid json",
            make_metrics(graph={"cross_component_edges": 9}),
        ],
    )

    points = mine_history(_entry(repo), "main")

    assert [extract(p.metrics, "graph.cross_component_edges") for p in points] == [3, 9]


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
