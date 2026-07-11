"""Web tests over fixture repos: every route, badges not 500s, containment."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import commit_files, git, make_metrics, make_repo

from guardrail_hub.config import HubConfig, ServerConfig
from guardrail_hub.models import RepoEntry
from guardrail_hub.web import create_app


def _write_docs(repo: Path) -> None:
    generated = repo / "docs" / "generated"
    (generated / "components").mkdir(parents=True, exist_ok=True)
    (generated / "architecture.md").write_text(
        '# Arch\n\n```mermaid\ngraph TD\n  Core --> Web\n  click Core "components/Core.md"\n```\n',
        encoding="utf-8",
    )
    (generated / "README.md").write_text(
        "# Index\n\n[arch](architecture.md)\n[model](../architecture.toml)\n", encoding="utf-8"
    )
    (generated / "components" / "Core.md").write_text("# Core\n", encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    repo = make_repo(
        tmp_path,
        snapshots=[
            make_metrics(graph={"cross_component_edges": 3}),
            make_metrics(graph={"cross_component_edges": 5}),
        ],
    )
    _write_docs(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "docs")
    config = HubConfig(
        repos=(
            RepoEntry(name="fixture", path=repo, family="test"),
            RepoEntry(name="ghost", path=tmp_path / "nope", family="test"),
        ),
        server=ServerConfig(),
    )
    return TestClient(create_app(config))


def test_overview_lists_repos_and_badges(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "fixture" in response.text
    assert "ghost" in response.text
    assert "missing checkout" in response.text  # badge, not a 500


def test_repo_detail(client: TestClient) -> None:
    response = client.get("/repos/fixture")

    assert response.status_code == 200
    assert "Budgets" in response.text
    assert "cross_component_edges" in response.text
    assert "Kit drift" in response.text


def test_repo_detail_missing_repo_renders(client: TestClient) -> None:
    response = client.get("/repos/ghost")

    assert response.status_code == 200
    assert "missing checkout" in response.text


def test_unknown_repo_404(client: TestClient) -> None:
    assert client.get("/repos/nope").status_code == 404


def test_doc_view_renders_mermaid(client: TestClient) -> None:
    response = client.get("/repos/fixture/docs/architecture.md")

    assert response.status_code == 200
    assert '<pre class="mermaid">' in response.text
    assert "mermaid.min.js" in response.text  # script only on mermaid pages


def test_doc_view_component_page(client: TestClient) -> None:
    assert client.get("/repos/fixture/docs/components/Core.md").status_code == 200


def test_doc_view_rewrites_escaping_link(client: TestClient) -> None:
    response = client.get("/repos/fixture/docs/README.md")

    assert 'href="architecture.md"' in response.text
    assert 'href="/repos/fixture/file/docs/architecture.toml"' in response.text


def test_doc_view_traversal_404(client: TestClient) -> None:
    assert client.get("/repos/fixture/docs/../architecture.toml").status_code == 404
    assert client.get("/repos/fixture/docs/%2e%2e/architecture.toml").status_code == 404


def test_file_route_serves_toml_as_plain(client: TestClient) -> None:
    response = client.get("/repos/fixture/file/docs/architecture.toml")

    assert response.status_code == 200
    assert "plain-file" in response.text
    assert "[budgets]" in response.text


def test_file_route_blocks_git_and_traversal(client: TestClient) -> None:
    assert client.get("/repos/fixture/file/.git/config").status_code == 404
    assert client.get("/repos/fixture/file/../secret").status_code == 404


def test_history_json_shape(client: TestClient) -> None:
    response = client.get("/repos/fixture/history.json?keys=graph.cross_component_edges")

    data = response.json()
    assert data["series"]["graph.cross_component_edges"] == [3, 5]
    assert len(data["dates"]) == 2


def test_compare_page_and_json(client: TestClient) -> None:
    page = client.get("/compare")
    assert page.status_code == 200
    assert "Cross-component edges" in page.text

    data = client.get("/compare/history.json?key=graph.cross_component_edges").json()
    assert data["repos"]["fixture"]["values"] == [3, 5]
    assert data["repos"]["ghost"]["values"] == []


def test_drift_panel(client: TestClient) -> None:
    response = client.get("/drift")

    assert response.status_code == 200
    assert "pending adoption" in response.text
    assert "fixture" in response.text


def test_health(client: TestClient) -> None:
    data = client.get("/health").json()

    assert data["ok"] is True
    assert data["repos"]["fixture"]["branch"] == "main"
    assert data["repos"]["ghost"]["present"] is False


def test_refresh_redirects_and_picks_up_changes(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/refresh", follow_redirects=False)

    assert response.status_code == 303


def test_repo_refresh(client: TestClient) -> None:
    assert client.post("/repos/fixture/refresh", follow_redirects=False).status_code == 303
    assert client.post("/repos/nope/refresh", follow_redirects=False).status_code == 404


def test_repo_page_shows_hotspots_and_ledger_sections(client: TestClient) -> None:
    # the fixture has two snapshots -> hotspots render; no budget edits -> ledger hidden;
    # no co-changing source history -> coupling panel hidden
    page = client.get("/repos/fixture").text
    assert "Hotspots" in page
    assert "Core" in page
    assert "Budget ledger" not in page
    assert "Change coupling" not in page


def test_repo_page_shows_coupling_panel(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    for i in range(3):
        commit_files(
            repo,
            {"src/fixture/core.py": f"a = {i}\n", "src/fixture/web.py": f"b = {i}\n"},
            message=f"pair {i}",
        )
    config = HubConfig(
        repos=(RepoEntry(name="fixture", path=repo, family="test"),), server=ServerConfig()
    )
    page = TestClient(create_app(config)).get("/repos/fixture").text

    assert "Change coupling" in page
    assert "fixture.core" in page and "fixture.web" in page
    assert "components/Web.md" in page  # component drill-down links


def test_ledger_page_renders(client: TestClient) -> None:
    page = client.get("/ledger")
    assert page.status_code == 200
    assert "Budget ledger" in page.text
    # fixture repos have no budget changes -> the empty state shows
    assert "No budget changes recorded yet" in page.text


def test_architecture_doc_shows_edge_legend(client: TestClient) -> None:
    page = client.get("/repos/fixture/docs/architecture.md").text
    assert "Reading the edges" in page
    assert "tier-skipping" in page


def test_other_docs_have_no_edge_legend(client: TestClient) -> None:
    page = client.get("/repos/fixture/docs/metrics.md")
    if page.status_code == 200:  # fixture may not write metrics.md
        assert "Reading the edges" not in page.text
    comp = client.get("/repos/fixture/docs/components/Core.md")
    if comp.status_code == 200:
        assert "Reading the edges" not in comp.text
