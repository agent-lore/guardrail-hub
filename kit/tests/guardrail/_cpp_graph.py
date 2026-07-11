"""Include-graph frontend for C++ projects (the ``language = "cpp"`` adapter).

Builds the same graph surface :mod:`grimp` provides for Python — a set of
module names plus direct-dependency lookup — from quoted ``#include``
directives, so every downstream generator (component diagram, metrics,
layering check) stays language-blind.

Conventions:

* A *module* is the merged ``.h``/``.cpp`` pair sharing a path stem
  (``net/control_client.{h,cpp}`` -> ``<root_package>.net.control_client``).
* Only quoted includes are graphed; angle includes are external by convention.
* A quoted include that does not resolve to a file under the source root is
  dropped as external — unless it matches a ``[cpp] virtual_includes`` prefix,
  which maps it to a synthetic module (e.g. build-dir generated protobuf
  headers), so a generated seam still shows up as a component.
"""

from __future__ import annotations

import re

from tests.guardrail._common import (
    ROOT_PACKAGE,
    SRC_ROOT,
    load_architecture,
    module_name_of,
    module_paths,
)

_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)


class IncludeGraph:
    """Duck-type of the grimp graph surface the generators rely on."""

    def __init__(self, edges: dict[str, set[str]], modules: set[str]) -> None:
        self._edges = edges
        self.modules = modules

    def find_modules_directly_imported_by(self, module: str) -> set[str]:
        return self._edges.get(module, set())


def _virtual_includes() -> dict[str, str]:
    """``[cpp] virtual_includes``: include-path prefix -> synthetic module name."""
    table = load_architecture().get("cpp", {}).get("virtual_includes", {})
    return {prefix: f"{ROOT_PACKAGE}.{name}" for prefix, name in table.items()}


def _resolve(include: str, virtual: dict[str, str]) -> str | None:
    """Module an include path points at (None = external, not graphed)."""
    target = SRC_ROOT / include
    if target.is_file():
        return module_name_of(target)
    for prefix, module in virtual.items():
        if include.startswith(prefix):
            return module
    return None


def build_include_graph() -> IncludeGraph:
    virtual = _virtual_includes()
    edges: dict[str, set[str]] = {}
    modules = set(virtual.values())
    for module, paths in module_paths().items():
        modules.add(module)
        deps = edges.setdefault(module, set())
        for path in paths:
            for include in _INCLUDE.findall(path.read_text(encoding="utf-8")):
                dep = _resolve(include, virtual)
                if dep is not None and dep != module:
                    deps.add(dep)
    return IncludeGraph(edges, modules)
