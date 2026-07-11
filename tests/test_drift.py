"""Unit tests for the drift engine: normalization, adapter matrix, alt paths."""

from __future__ import annotations

import shutil
from pathlib import Path

from tests.conftest import make_monorepo, make_repo

from guardrail_hub.drift import compare_repo, installed_version, normalized_ast_dump
from guardrail_hub.kit import kit_root, load_manifest
from guardrail_hub.models import RepoEntry


def _apply_core_kit(repo: Path, *, layering_at_alt: bool = False) -> None:
    """Copy the canonical core kit files into a fixture repo verbatim."""
    for kit_file in load_manifest():
        if kit_file.role.startswith("adapter-"):
            continue
        dest_rel = kit_file.path
        if layering_at_alt and kit_file.alt_paths and "layering" in kit_file.path:
            dest_rel = kit_file.alt_paths[0]
        dest = repo / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kit_root() / kit_file.path, dest)
    (repo / "tests" / "guardrail" / "KIT_VERSION").write_text("1.0.0\n", encoding="utf-8")


def _kitted_repo(tmp_path: Path, **kwargs: bool) -> RepoEntry:
    repo = make_repo(tmp_path)
    _apply_core_kit(repo, **kwargs)
    return RepoEntry(name="fixture", path=repo, family="test")


def _statuses(entry: RepoEntry) -> dict[str, str]:
    return {f.path: f.status for f in compare_repo(entry).files}


# ── normalization ──────────────────────────────────────────────────────


def test_normalization_ignores_formatting_comments_docstrings() -> None:
    a = '''
def f(x, y):
    """Original docstring."""
    return x + y  # comment


class C:
    """Class doc."""

    def m(self):
        """Method doc."""
        return 1
'''
    b = """
def f(
    x,
    y,
):
    "Rewritten docstring, different quotes."
    return x + y


class C:
    def m(self):
        return 1
"""
    assert normalized_ast_dump(a) == normalized_ast_dump(b)


def test_normalization_catches_logic_change() -> None:
    assert normalized_ast_dump("def f(x):\n    return x + 1\n") != normalized_ast_dump(
        "def f(x):\n    return x + 2\n"
    )


def test_docstring_only_module_normalizes() -> None:
    assert normalized_ast_dump('"""Just a docstring."""\n') == normalized_ast_dump(
        '"""Different docstring."""\n'
    )


def test_explicit_string_concat_folds_to_implicit() -> None:
    implicit = 'LINES = ["start of a long sentence continued", "other"]\n'
    explicit = 'LINES = ["start of a long " + "sentence continued", "other"]\n'
    chained = 'LINES = ["start of a " + "long " + "sentence continued", "other"]\n'

    assert normalized_ast_dump(implicit) == normalized_ast_dump(explicit)
    assert normalized_ast_dump(implicit) == normalized_ast_dump(chained)
    assert normalized_ast_dump(implicit) != normalized_ast_dump(
        'LINES = ["different text entirely", "other"]\n'
    )


# ── compare_repo ───────────────────────────────────────────────────────


def test_pristine_core_application_is_clean(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)

    report = compare_repo(entry)

    assert report.installed_version == "1.0.0"
    assert report.clean
    assert {f.status for f in report.files} == {"same"}
    # adapters are not enabled in the fixture architecture.toml -> not reported
    assert not any(f.role.startswith("adapter-") for f in report.files)


def test_reformatted_and_regenericized_file_is_same(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    target = entry.path / "tests" / "guardrail" / "_common.py"
    source = target.read_text(encoding="utf-8")
    # simulate the 88-width ports: new docstring + an inline comment + reflow
    source = (
        source.replace('"""', '"""REWRITTEN.\n\n', 1) + "\n# trailing comment added by a port\n"
    )
    target.write_text(source, encoding="utf-8")

    assert _statuses(entry)["tests/guardrail/_common.py"] == "same"


def test_logic_edit_differs(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    target = entry.path / "scripts" / "metrics_diff.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\nEXTRA_CONSTANT = 42\n", encoding="utf-8"
    )

    assert _statuses(entry)["scripts/metrics_diff.py"] == "differs"


def test_missing_core_file(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    (entry.path / "tests" / "guardrail" / "_index.py").unlink()

    assert _statuses(entry)["tests/guardrail/_index.py"] == "missing"


def test_unparseable_file_is_error(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    (entry.path / "tests" / "guardrail" / "_index.py").write_text("def broken(:\n")

    assert _statuses(entry)["tests/guardrail/_index.py"] == "error"


def test_layering_contract_alt_path(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path, layering_at_alt=True)

    assert _statuses(entry)["tests/guardrail/test_layering_contract.py"] == "same"


def test_repo_local_addition_is_extra(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    (entry.path / "tests" / "guardrail" / "test_no_stubs.py").write_text("def test_ok(): pass\n")

    report = compare_repo(entry)
    extras = [f for f in report.files if f.status == "extra"]

    assert [f.path for f in extras] == ["tests/guardrail/test_no_stubs.py"]
    assert report.clean  # extras don't dirty the report


def test_adapter_expected_when_configured(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    arch = entry.path / "docs" / "architecture.toml"
    arch.write_text(
        arch.read_text(encoding="utf-8") + '\n[tool_catalog]\ninclude_modules = ["fixture.web"]\n',
        encoding="utf-8",
    )

    statuses = _statuses(entry)

    assert statuses["tests/guardrail/_tool_catalog.py"] == "missing"
    assert statuses["tests/guardrail/test_tool_catalog.py"] == "missing"
    assert "tests/guardrail/_containers.py" not in statuses  # still unconfigured


def test_adapter_file_without_config_is_extra(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    shutil.copyfile(
        kit_root() / "tests/guardrail/_containers.py",
        entry.path / "tests" / "guardrail" / "_containers.py",
    )

    statuses = _statuses(entry)

    assert statuses["tests/guardrail/_containers.py"] == "extra"


def test_pre_hub_repo_version(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    assert installed_version(repo) == "pre-hub"


# ── cpp adapter & monorepo subdir ─────────────────────────────────


def _set_language_cpp(entry: RepoEntry) -> None:
    arch = entry.root / "docs" / "architecture.toml"
    text = arch.read_text(encoding="utf-8").replace(
        'root_package = "fixture"', 'root_package = "fixture"\nlanguage = "cpp"'
    )
    arch.write_text(text, encoding="utf-8")


def test_cpp_adapter_expected_for_cpp_language(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    _set_language_cpp(entry)

    assert _statuses(entry)["tests/guardrail/_cpp_graph.py"] == "missing"

    shutil.copyfile(
        kit_root() / "tests/guardrail/_cpp_graph.py",
        entry.path / "tests" / "guardrail" / "_cpp_graph.py",
    )
    assert _statuses(entry)["tests/guardrail/_cpp_graph.py"] == "same"


def test_cpp_adapter_file_without_cpp_language_is_extra(tmp_path: Path) -> None:
    entry = _kitted_repo(tmp_path)
    shutil.copyfile(
        kit_root() / "tests/guardrail/_cpp_graph.py",
        entry.path / "tests" / "guardrail" / "_cpp_graph.py",
    )

    assert _statuses(entry)["tests/guardrail/_cpp_graph.py"] == "extra"


def test_subdir_entry_compares_kit_under_subdir(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, subdir="sub")
    _apply_core_kit(repo / "sub")
    entry = RepoEntry(name="mono", path=repo, family="test", subdir="sub")

    report = compare_repo(entry)

    assert report.installed_version == "1.0.0"
    assert all(f.status in ("same", "extra") for f in report.files), [
        (f.path, f.status) for f in report.files if f.status not in ("same", "extra")
    ]
