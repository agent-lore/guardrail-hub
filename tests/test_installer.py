"""Installer tests: prefill golden, apply semantics, refuse-don't-edit."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from guardrail_hub.errors import KitError
from guardrail_hub.installer import apply_kit, build_architecture_skeleton
from guardrail_hub.kit import kit_version

GOLDEN = Path(__file__).parent / "golden"

PYPROJECT = """
[project]
name = "fixture-proj"

[tool.importlinter]
root_package = "fixture_proj"

[[tool.importlinter.contracts]]
name = "Foundation must not import Core or Entrypoints"
type = "forbidden"
source_modules = ["fixture_proj.config", "fixture_proj.errors", "fixture_proj.types"]
forbidden_modules = ["fixture_proj.engine", "fixture_proj.store", "fixture_proj.web", "fixture_proj.cli"]

[[tool.importlinter.contracts]]
name = "Core must not import Entrypoints"
type = "forbidden"
source_modules = ["fixture_proj.engine", "fixture_proj.store"]
forbidden_modules = ["fixture_proj.web", "fixture_proj.cli"]
"""


def make_target(tmp_path: Path, pyproject: str = PYPROJECT, src_layout: bool = True) -> Path:
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True)
    (target / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    pkg = (target / "src" / "fixture_proj") if src_layout else (target / "fixture_proj")
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    return target


def test_prefill_matches_golden() -> None:
    out = build_architecture_skeleton(tomllib.loads(PYPROJECT), "fixture_proj", "src")

    assert out == (GOLDEN / "architecture_prefilled.toml").read_text(encoding="utf-8")


def test_prefill_without_importlinter_is_generic_todo() -> None:
    out = build_architecture_skeleton({"project": {"name": "x"}}, "x", "src")

    assert "# TODO: map every module to a component" in out
    assert "[tiers]" in out and "[budgets]" in out


def test_apply_writes_kit_and_skeleton(tmp_path: Path) -> None:
    target = make_target(tmp_path)

    report = apply_kit(target)

    assert (target / "tests" / "guardrail" / "_common.py").is_file()
    assert (target / "tests" / "guardrail" / "test_metrics_budgets.py").is_file()
    assert (target / "scripts" / "metrics_history.py").is_file()
    assert (target / "tests" / "test_metrics_diff.py").is_file()
    version = (target / "tests" / "guardrail" / "KIT_VERSION").read_text(encoding="utf-8")
    assert version.strip() == kit_version()
    arch = (target / "docs" / "architecture.toml").read_text(encoding="utf-8")
    assert arch == (GOLDEN / "architecture_prefilled.toml").read_text(encoding="utf-8")
    # adapters omitted by default
    assert not (target / "tests" / "guardrail" / "_tool_catalog.py").exists()
    assert not (target / "tests" / "guardrail" / "_containers.py").exists()
    # report carries the wiring snippets + checklist
    assert "diagrams:" in report and "Diagram drift" in report
    assert "Run `make diagrams`" in report
    assert 'pythonpath = ["."]' in report


def test_apply_with_adapters(tmp_path: Path) -> None:
    target = make_target(tmp_path)

    apply_kit(target, with_tool_catalog=True, with_containers=True)

    assert (target / "tests" / "guardrail" / "_tool_catalog.py").is_file()
    assert (target / "tests" / "guardrail" / "test_container_diagram.py").is_file()


def test_apply_refuses_on_conflicts_and_writes_nothing(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    existing = target / "tests" / "guardrail" / "_common.py"
    existing.parent.mkdir(parents=True)
    existing.write_text("# pre-existing\n", encoding="utf-8")
    (target / "docs").mkdir()
    (target / "docs" / "architecture.toml").write_text("# mine\n", encoding="utf-8")

    with pytest.raises(KitError) as exc:
        apply_kit(target)

    message = str(exc.value)
    assert "tests/guardrail/_common.py" in message
    assert "docs/architecture.toml" in message
    assert existing.read_text(encoding="utf-8") == "# pre-existing\n"
    assert not (target / "scripts" / "metrics_diff.py").exists()  # nothing else written


def test_apply_detects_flat_layout(tmp_path: Path) -> None:
    target = make_target(tmp_path, src_layout=False)

    apply_kit(target)

    arch = (target / "docs" / "architecture.toml").read_text(encoding="utf-8")
    assert 'src_layout   = ""' in arch


def test_apply_requires_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    with pytest.raises(KitError, match="not a git repository"):
        apply_kit(plain)


def test_apply_requires_detectable_package(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    (target / "pyproject.toml").write_text('[project]\nname = "other-name"\n', encoding="utf-8")

    with pytest.raises(KitError, match="found neither"):
        apply_kit(target)


def test_root_package_override(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    (target / "pyproject.toml").write_text('[project]\nname = "misleading"\n', encoding="utf-8")

    apply_kit(target, root_package="fixture_proj")

    arch = (target / "docs" / "architecture.toml").read_text(encoding="utf-8")
    assert 'root_package = "fixture_proj"' in arch


# ── cpp targets ────────────────────────────────────────────────────


def make_cpp_target(tmp_path: Path) -> Path:
    target = tmp_path / "cpp-target"
    (target / "src" / "util").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True)
    (target / "src" / "util" / "log.h").write_text("#pragma once\n", encoding="utf-8")
    return target


def test_apply_cpp_target(tmp_path: Path) -> None:
    target = make_cpp_target(tmp_path)

    report = apply_kit(target, language="cpp", root_package="agent")

    assert (target / "tests" / "guardrail" / "_cpp_graph.py").is_file()
    assert (target / "tests" / "guardrail" / "_common.py").is_file()
    arch = (target / "docs" / "architecture.toml").read_text(encoding="utf-8")
    assert 'language     = "cpp"' in arch
    assert 'root_package = "agent"' in arch
    assert "[cpp.virtual_includes]" in arch
    # the cpp checklist replaces the import-linter step with the tier-order rule
    assert "No import-linter needed for C++" in report
    assert "pytest.ini" in report


def test_apply_cpp_requires_root_package(tmp_path: Path) -> None:
    target = make_cpp_target(tmp_path)

    with pytest.raises(KitError, match="--root-package is required"):
        apply_kit(target, language="cpp")


def test_apply_rejects_unknown_language(tmp_path: Path) -> None:
    target = make_cpp_target(tmp_path)

    with pytest.raises(KitError, match="unsupported language"):
        apply_kit(target, language="rust", root_package="agent")
