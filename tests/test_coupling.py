"""Change-coupling tests: path mapping, git mining, and pair extraction."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import commit_files, git, make_monorepo, make_repo

from guardrail_hub import coupling, gitio
from guardrail_hub.archmap import ArchMap, load_archmap
from guardrail_hub.models import RepoEntry


def entry_for(repo: Path, subdir: str = "") -> RepoEntry:
    return RepoEntry(name="fixture", path=repo, family="test", subdir=subdir)


# --- archmap ------------------------------------------------------------------ #
def test_archmap_python_module_naming(fixture_repo: Path) -> None:
    arch = load_archmap(entry_for(fixture_repo))

    assert arch is not None
    assert arch.module_of("src/fixture/core.py") == "fixture.core"
    assert arch.module_of("src/fixture/__init__.py") == "fixture"
    assert arch.module_of("src/fixture/web/views.py") == "fixture.web.views"
    assert arch.module_of("docs/architecture.toml") is None
    assert arch.module_of("src/fixture/data.json") is None
    assert arch.module_of("tests/test_core.py") is None


def test_archmap_component_longest_prefix(fixture_repo: Path) -> None:
    arch = load_archmap(entry_for(fixture_repo))

    assert arch is not None
    assert arch.component_of("fixture.core") == "Core"
    assert arch.component_of("fixture.core.sub") == "Core"
    assert arch.component_of("fixture.unmapped") is None


def test_archmap_cpp_merges_header_and_impl() -> None:
    arch = ArchMap(
        root_package="agent",
        language="cpp",
        src_prefix="client/src",
        components={"Net": ("agent.net",)},
    )

    assert arch.module_of("client/src/net/control.h") == "agent.net.control"
    assert arch.module_of("client/src/net/control.cpp") == "agent.net.control"
    assert arch.module_of("client/src/net/readme.md") is None


def test_archmap_missing_config_is_none(tmp_path: Path) -> None:
    assert load_archmap(entry_for(tmp_path)) is None


# --- gitio.first_parent_changes ------------------------------------------------ #
def test_first_parent_changes_lists_paths_newest_first(fixture_repo: Path) -> None:
    commit_files(fixture_repo, {"src/fixture/core.py": "a = 1\n"}, message="one")
    commit_files(
        fixture_repo,
        {"src/fixture/core.py": "a = 2\n", "src/fixture/web.py": "b = 1\n"},
        message="two",
    )

    commits = gitio.first_parent_changes(fixture_repo, "HEAD", 10)

    assert commits[0] == ["src/fixture/core.py", "src/fixture/web.py"]
    assert commits[1] == ["src/fixture/core.py"]


def test_first_parent_changes_renames_count_as_new_path(fixture_repo: Path) -> None:
    commit_files(fixture_repo, {"src/fixture/old.py": "x = 1\n" * 30}, message="add")
    git(fixture_repo, "mv", "src/fixture/old.py", "src/fixture/new.py")
    git(fixture_repo, "commit", "-q", "-m", "rename")

    commits = gitio.first_parent_changes(fixture_repo, "HEAD", 10)

    assert commits[0] == ["src/fixture/new.py"]
    assert "src/fixture/old.py" not in commits[0]


def test_first_parent_changes_respects_commit_cap(fixture_repo: Path) -> None:
    for i in range(3):
        commit_files(fixture_repo, {"src/fixture/core.py": f"a = {i}\n"}, message=f"c{i}")

    assert len(gitio.first_parent_changes(fixture_repo, "HEAD", 2)) == 2


# --- coupling.mine_coupling ---------------------------------------------------- #
def test_cross_component_pair_needs_min_support(fixture_repo: Path) -> None:
    entry = entry_for(fixture_repo)
    for i in range(coupling.MIN_CO_CHANGES - 1):
        commit_files(
            fixture_repo,
            {"src/fixture/core.py": f"a = {i}\n", "src/fixture/web.py": f"b = {i}\n"},
            message=f"pair {i}",
        )

    assert coupling.mine_coupling(entry, "HEAD") == ()

    commit_files(
        fixture_repo,
        {"src/fixture/core.py": "a = 9\n", "src/fixture/web.py": "b = 9\n"},
        message="pair 3",
    )
    pairs = coupling.mine_coupling(entry, "HEAD")

    assert len(pairs) == 1
    pair = pairs[0]
    assert (pair.module_a, pair.module_b) == ("fixture.core", "fixture.web")
    assert (pair.component_a, pair.component_b) == ("Core", "Web")
    assert pair.co_changes == coupling.MIN_CO_CHANGES
    assert pair.strength == 1.0


def test_solo_changes_dilute_strength(fixture_repo: Path) -> None:
    entry = entry_for(fixture_repo)
    for i in range(3):
        commit_files(
            fixture_repo,
            {"src/fixture/core.py": f"a = {i}\n", "src/fixture/web.py": f"b = {i}\n"},
            message=f"pair {i}",
        )
    for i in range(3):
        commit_files(fixture_repo, {"src/fixture/web.py": f"b = 9{i}\n"}, message=f"solo {i}")

    (pair,) = coupling.mine_coupling(entry, "HEAD")

    assert pair.changes_a == 3
    assert pair.changes_b == 6
    assert pair.strength == 1.0  # min(changes) is still the co-changing side


def test_same_component_and_unmapped_pairs_are_excluded(fixture_repo: Path) -> None:
    entry = entry_for(fixture_repo)
    for i in range(3):
        commit_files(
            fixture_repo,
            {"src/fixture/web/a.py": f"a = {i}\n", "src/fixture/web/b.py": f"b = {i}\n"},
            message=f"same-component {i}",
        )
    for i in range(3):
        commit_files(
            fixture_repo,
            {"src/fixture/core.py": f"c = {i}\n", "src/fixture/stray.py": f"s = {i}\n"},
            message=f"unmapped {i}",
        )

    assert coupling.mine_coupling(entry, "HEAD") == ()


def test_mega_commits_are_skipped(fixture_repo: Path) -> None:
    entry = entry_for(fixture_repo)
    for i in range(3):
        commit_files(
            fixture_repo,
            {"src/fixture/core.py": f"a = {i}\n", "src/fixture/web.py": f"b = {i}\n"},
            message=f"pair {i}",
        )
    sweep = {
        f"src/fixture/web/mod{i:02d}.py": "x = 1\n" for i in range(coupling.MAX_COMMIT_MODULES)
    }
    commit_files(fixture_repo, {**sweep, "src/fixture/core.py": "a = 99\n"}, message="sweep")

    (pair,) = coupling.mine_coupling(entry, "HEAD")

    assert pair.co_changes == 3  # the sweep did not inflate the pair
    assert pair.changes_a == 3  # nor the per-module change counts


def test_monorepo_subdir_paths_map(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, subdir="server")
    entry = entry_for(repo, subdir="server")
    for i in range(3):
        commit_files(
            repo,
            {
                "server/src/fixture/core.py": f"a = {i}\n",
                "server/src/fixture/web.py": f"b = {i}\n",
                "ui/app.ts": f"// {i}\n",
            },
            message=f"pair {i}",
        )

    (pair,) = coupling.mine_coupling(entry, "HEAD")

    assert (pair.module_a, pair.module_b) == ("fixture.core", "fixture.web")


def test_missing_architecture_toml_yields_empty(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "docs" / "architecture.toml").unlink()

    assert coupling.mine_coupling(entry_for(repo), "HEAD") == ()
