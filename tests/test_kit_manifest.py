"""Manifest ↔ kit/ directory closure, both directions."""

from __future__ import annotations

from guardrail_hub.kit import kit_root, kit_version, load_manifest


def test_every_manifest_file_exists_in_kit() -> None:
    root = kit_root()
    missing = [f.path for f in load_manifest() if not (root / f.path).is_file()]

    assert not missing, f"manifest names files absent from kit/: {missing}"


def test_every_kit_file_is_in_manifest() -> None:
    root = kit_root()
    declared = {f.path for f in load_manifest()}
    on_disk = {
        str(p.relative_to(root))
        for sub in ("tests", "scripts")
        for p in (root / sub).rglob("*")
        if p.is_file() and p.suffix in (".py", ".md")
    }
    undeclared = sorted(on_disk - declared)

    assert not undeclared, f"kit/ files missing from manifest.toml: {undeclared}"


def test_kit_version_is_semverish() -> None:
    version = kit_version()

    assert version and all(part.isdigit() for part in version.split("."))


def test_roles_are_known() -> None:
    known = {"core", "adapter-tool-catalog", "adapter-containers", "doc"}

    assert {f.role for f in load_manifest()} <= known


def test_kit_files_parse_as_python() -> None:
    import ast

    root = kit_root()
    for f in load_manifest():
        if f.is_python:
            ast.parse((root / f.path).read_text(encoding="utf-8"), filename=f.path)
