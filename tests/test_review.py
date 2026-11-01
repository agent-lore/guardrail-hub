"""Committed revision comparisons and trustworthy optional evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import (
    commit_files,
    commit_snapshot,
    git,
    make_metrics,
    make_monorepo,
    make_repo,
)

from guardrail_hub.config import HubConfig, ServerConfig
from guardrail_hub.models import RepoEntry
from guardrail_hub.review import compare
from guardrail_hub.web import create_app


def measured(score: int) -> dict:
    return make_metrics(
        measurement={
            "definitions": {"complexity": 1},
            "language": "python",
            "scope": {},
            "tools": {},
        },
        functions={"fixture.core.work": {"complexity": score, "length": 30}},
        components={"Core": ["fixture.core"], "Web": ["fixture.web"]},
        graph={"edges": []},
    )


def test_revision_page_and_api_show_specific_regression(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=[measured(11), measured(18)])
    entry = RepoEntry(name="fixture", path=repo)
    client = TestClient(create_app(HubConfig(repos=(entry,), server=ServerConfig())))
    data = client.get("/repos/fixture/changes.json?base=HEAD~1&head=HEAD").json()
    assert data["error"] == ""
    assert data["violations"][0]["status"] == "worsened"
    assert data["violations"][0]["target"] == "fixture.core.work"
    page = client.get("/repos/fixture/changes?base=HEAD~1&head=HEAD")
    assert page.status_code == 200
    assert "worsened" in page.text and "fixture.core.work" in page.text
    assert "mermaid" in page.text


def test_revision_comparison_uses_committed_config_not_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=[measured(11), measured(18)])
    arch = repo / "docs/architecture.toml"
    original = arch.read_text()
    commit_files(
        repo, {"docs/architecture.toml": original + "\n[review]\nfunction_complexity = 20\n"}
    )
    arch.write_text(original + "\n[review]\nfunction_complexity = 1\n")
    result = compare(RepoEntry(name="fixture", path=repo), "HEAD~1", "HEAD")
    assert not result["error"]
    assert result["violations"] == []
    assert any(r["key"] == "limits" for r in result["configuration"])


def test_revision_errors_and_option_like_refs_are_badges(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    entry = RepoEntry(name="fixture", path=repo)
    assert compare(entry, "--help", "HEAD")["error"]
    assert compare(entry, "no-such-ref", "HEAD")["error"]
    commit_snapshot(repo, "{invalid")
    assert compare(entry, "HEAD~1", "HEAD")["error"]


def test_revision_comparison_supports_monorepo_subdirs(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, snapshots=[measured(11), measured(18)])
    result = compare(RepoEntry(name="server", path=repo, subdir="server"), "HEAD~1", "HEAD")
    assert not result["error"]
    assert result["violations"][0]["status"] == "worsened"


def test_revision_rejects_stale_source_snapshot(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit_files(repo, {"src/fixture/core.py": "x = 1\n"})
    sources = {"src/fixture/core.py": hashlib.sha256(b"x = 1\n").hexdigest()}
    metrics = measured(11)
    metrics["measurement"].update(
        source_hash=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        scope={"source_root": "src/fixture", "source_suffixes": [".py"]},
    )
    commit_snapshot(repo, metrics)
    base = git(repo, "rev-parse", "HEAD").strip()
    commit_files(repo, {"src/fixture/core.py": "x = 2\n"})
    result = compare(RepoEntry(name="fixture", path=repo), base, "HEAD")
    assert "stale" in result["error"]


def test_evidence_is_source_bound_and_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.guardrail import _measurement

    monkeypatch.setattr(_measurement, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        _measurement, "load_architecture", lambda: {"quality": {"evidence_path": "evidence.json"}}
    )
    evidence = {
        "source_hash": "a",
        "branch_coverage": {
            "covered": 8,
            "total": 10,
            "scope": "changed branches vs base",
            "tool": "coverage.py",
            "reference": "run 123",
        },
    }
    (tmp_path / "evidence.json").write_text(json.dumps(evidence))
    assert _measurement.quality_evidence({"source_hash": "a"})["branch_coverage"]["value"] == 80
    stale = _measurement.quality_evidence({"source_hash": "b"})["branch_coverage"]
    assert stale["status"] == "stale" and stale["value"] is None
    assert _measurement.quality_evidence({"source_hash": "a"})["mutation"]["status"] == "missing"
    monkeypatch.setattr(
        _measurement, "load_architecture", lambda: {"quality": {"evidence_path": "../outside.json"}}
    )
    assert (
        _measurement.quality_evidence({"source_hash": "a"})["branch_coverage"]["status"]
        == "invalid"
    )


def test_generator_records_all_functions_and_excludes_kit_test_volume() -> None:
    from tests.guardrail._metrics_toolkit import compute_metrics

    metrics = compute_metrics()
    assert len(metrics["functions"]) == metrics["complexity"]["total_functions"]
    assert len(metrics["functions"]) > len(metrics["complexity"]["top_functions"])
    from guardrail_hub.kit import kit_version

    assert metrics["measurement"]["analyzer_version"] == kit_version()
    expected = sum(
        len(p.read_text().splitlines())
        for p in Path("tests").rglob("*.py")
        if "guardrail" not in p.parts
        and p.name not in {"test_metrics_diff.py", "test_metrics_history.py", "test_quality.py"}
    )
    assert metrics["tests"]["test_lines"] == expected


def test_quality_page_shows_outcomes_and_missing_evidence(tmp_path: Path) -> None:
    metrics = measured(11)
    metrics["quality"] = {
        "outcomes": [
            {
                "intervention": "Parser cleanup",
                "measure": "regressions per 20 changes",
                "before": 4,
                "after": 1,
                "baseline": "release A",
                "followup": "release B",
                "reference": "issue 123",
            }
        ]
    }
    repo = make_repo(tmp_path, snapshots=[metrics])
    client = TestClient(
        create_app(HubConfig(repos=(RepoEntry(name="fixture", path=repo),), server=ServerConfig()))
    )
    page = client.get("/repos/fixture")
    assert page.status_code == 200
    assert "Parser cleanup" in page.text and "issue 123" in page.text
    assert "Branch coverage" in page.text and "missing" in page.text


def test_historical_source_view_reads_requested_revision_and_blocks_escape(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit_files(repo, {"src/fixture/core.py": "value = 'old'\n"})
    previous = git(repo, "rev-parse", "HEAD").strip()
    commit_files(repo, {"src/fixture/core.py": "value = 'new'\n"})
    client = TestClient(
        create_app(HubConfig(repos=(RepoEntry(name="fixture", path=repo),), server=ServerConfig()))
    )
    page = client.get(f"/repos/fixture/revision/{previous}/file/src/fixture/core.py")
    assert page.status_code == 200 and "old" in page.text and "new" not in page.text
    assert client.get(f"/repos/fixture/revision/{previous}/file/%2e%2e/secret").status_code == 404
    assert client.get(f"/repos/fixture/revision/{previous}/file/.git/config").status_code == 404


def test_invalid_nested_metrics_render_badge(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=[make_metrics(graph={"components": {"Core": 7}})])
    client = TestClient(
        create_app(HubConfig(repos=(RepoEntry(name="fixture", path=repo),), server=ServerConfig()))
    )
    page = client.get("/repos/fixture")
    assert page.status_code == 200
    assert "Invalid metrics/configuration" in page.text


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_source_only_worktree_edit_invalidates_freshness_without_refresh(
    tmp_path: Path, newline: str
) -> None:
    repo = make_repo(tmp_path)
    source = "value = 1\n"
    commit_files(repo, {"src/fixture/core.py": source})
    sources = {"src/fixture/core.py": hashlib.sha256(source.encode()).hexdigest()}
    metrics = measured(11)
    metrics["measurement"].update(
        source_hash=hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        scope={"source_root": "src/fixture", "source_suffixes": [".py"]},
    )
    commit_snapshot(repo, metrics)
    (repo / "src/fixture/core.py").write_bytes(source.replace("\n", newline).encode())
    client = TestClient(
        create_app(HubConfig(repos=(RepoEntry(name="fixture", path=repo),), server=ServerConfig()))
    )
    assert "Snapshot matches" in client.get("/repos/fixture").text
    (repo / "src/fixture/core.py").write_text("value = 2\n")
    assert "Source changed since" in client.get("/repos/fixture").text
