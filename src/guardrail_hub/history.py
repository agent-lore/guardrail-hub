"""Metric time series mined from the git history of metrics.json.

Because every kit repo commits ``docs/generated/metrics.json`` and regenerates
it deterministically, its git history *is* the architecture-metrics time
series — the same insight as the kit's ``scripts/metrics_history.py``, run here
over the hub's git seam. Mining walks first-parent history of the repo's
default branch so a feature-branch checkout still charts mainline.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Mapping
from typing import Any

from guardrail_hub import gitio
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.models import MetricPoint, RepoEntry

SNAPSHOT_PATH = "docs/generated/metrics.json"


def snapshot_path(entry: RepoEntry) -> str:
    """Repo-relative path of the metrics snapshot (subdir-aware for monorepos)."""
    return f"{entry.subdir}/{SNAPSHOT_PATH}" if entry.subdir else SNAPSHOT_PATH


def mine_history(entry: RepoEntry, ref: str) -> tuple[MetricPoint, ...]:
    """One MetricPoint per first-parent commit of ``ref`` touching the snapshot.

    Commits where the snapshot is missing or malformed are skipped, matching
    the kit's own history walker.
    """
    points: list[MetricPoint] = []
    rel = snapshot_path(entry)
    for sha, date_str in gitio.first_parent_log(entry.path, ref, rel):
        try:
            metrics = json.loads(gitio.show_file(entry.path, sha, rel))
        except RepoAccessError:
            continue  # commit removed the file (or predates it)
        except json.JSONDecodeError:
            continue  # malformed snapshot at this commit; skip, don't crash the walk
        if not isinstance(metrics, dict):
            continue
        points.append(
            MetricPoint(sha=sha, date=datetime.date.fromisoformat(date_str), metrics=metrics)
        )
    return tuple(points)


def extract(metrics: Mapping[str, Any], dotted_key: str) -> Any:
    """Value at a dotted path; a list-valued path is summarized via ``.count``.

    Same convention as the kit's scripts. Returns None for missing paths so
    charts and tables can render gaps instead of crashing on schema variance
    (e.g. ``mcp.tools`` exists only in repos with the tool-catalog adapter).
    """
    value: Any = metrics
    for part in dotted_key.split("."):
        if part == "count" and isinstance(value, list):
            return len(value)
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def series(points: tuple[MetricPoint, ...], keys: list[str]) -> dict[str, Any]:
    """Chart-ready shape: dates/shas plus one value list per requested key."""
    return {
        "dates": [p.date.isoformat() for p in points],
        "shas": [p.sha for p in points],
        "series": {key: [extract(p.metrics, key) for p in points] for key in keys},
    }
