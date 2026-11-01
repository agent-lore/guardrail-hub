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
import math
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

    Missing or malformed snapshots remain explicit gaps with commit identity.
    """
    points: list[MetricPoint] = []
    rel = snapshot_path(entry)
    for sha, timestamp in gitio.first_parent_commits(entry.path, ref, rel):
        status, error = "measured", ""
        metrics = {}
        try:
            metrics = json.loads(gitio.show_file(entry.path, sha, rel))
            if not isinstance(metrics, dict):
                raise ValueError("snapshot is not a JSON object")
            for key in ("graph", "size", "complexity", "measurement", "quality", "tests"):
                if key in metrics and not isinstance(metrics[key], dict):
                    raise ValueError(f"invalid snapshot section: {key}")
            if not isinstance(metrics.get("measurement", {}).get("capabilities", {}), dict):
                raise ValueError("invalid measurement capabilities")
        except RepoAccessError as exc:
            status, error, metrics = "missing", str(exc), {}
        except (ValueError, TypeError) as exc:
            status, error, metrics = "invalid", str(exc), {}
        points.append(
            MetricPoint(
                sha=sha,
                date=datetime.datetime.fromisoformat(timestamp).date(),
                metrics=metrics,
                timestamp=timestamp,
                status=status,
                error=error,
            )
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
    capabilities = metrics.get("measurement", {}).get("capabilities", {})
    if capabilities.get(dotted_key, capabilities.get(dotted_key.split(".")[0])) == "unsupported":
        return None
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
        "timestamps": [p.timestamp or p.date.isoformat() + "T00:00:00+00:00" for p in points],
        "statuses": [p.status for p in points],
        "errors": [p.error for p in points],
        "measurements": [p.metrics.get("measurement", {}) for p in points],
        "shas": [p.sha for p in points],
        "series": {
            key: [
                extract(p.metrics, key) if value_status(p, key) == "measured" else None
                for p in points
            ]
            for key in keys
        },
        "value_statuses": {key: [value_status(p, key) for p in points] for key in keys},
        "boundaries": [
            i
            for i in range(1, len(points))
            if points[i - 1].metrics.get("measurement", {})
            != points[i].metrics.get("measurement", {})
            and measurement_basis(points[i - 1]) != measurement_basis(points[i])
        ],
    }


def measurement_basis(point: MetricPoint) -> dict:
    metadata = dict(point.metrics.get("measurement", {}))
    metadata.pop("source_hash", None)
    return metadata


def value_status(point: MetricPoint, key: str) -> str:
    if point.status != "measured":
        return point.status
    section = key.split(".")[0]
    capabilities = point.metrics.get("measurement", {}).get("capabilities", {})
    if capabilities.get(key, capabilities.get(section)) == "unsupported":
        return "unsupported"
    if section == "quality" and len(key.split(".")) > 1:
        evidence = point.metrics.get("quality", {}).get(key.split(".")[1], {})
        if evidence.get("status", "missing") != "measured":
            return evidence.get("status", "missing")
    value = extract(point.metrics, key)
    if value is None:
        return "missing"
    return "measured" if type(value) in (int, float) and math.isfinite(value) else "invalid"
