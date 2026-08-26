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
    Keys that themselves contain dots — module names under ``modules.`` — are
    matched greedily, longest joined run first: at each mapping level the
    longest dot-joined run of remaining segments that IS a key there is
    consumed (so ``modules.pkg.mod.sloc`` finds the ``pkg.mod`` entry even
    though the package module ``pkg`` is a sibling key).
    """
    value: Any = metrics
    parts = dotted_key.split(".")
    i = 0
    while i < len(parts):
        if parts[i] == "count" and isinstance(value, list):
            return len(value)
        if not isinstance(value, Mapping):
            return None
        for j in range(len(parts), i, -1):
            joined = ".".join(parts[i:j])
            if joined in value:
                value = value[joined]
                i = j
                break
        else:
            return None
    return value


def series(points: tuple[MetricPoint, ...], keys: list[str]) -> dict[str, Any]:
    """Chart-ready shape: dates/shas plus one value list per requested key."""
    return {
        "dates": [p.date.isoformat() for p in points],
        "shas": [p.sha for p in points],
        "series": {key: [extract(p.metrics, key) for p in points] for key in keys},
    }
