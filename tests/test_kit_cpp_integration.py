"""End-to-end: the kit's generators run against a real (synthetic) C++ project.

Applies the canonical kit with ``language = "cpp"`` into a scratch git repo,
authors a real ``docs/architecture.toml`` over a tiny .h/.cpp tree, then runs
``pytest tests/guardrail`` in a subprocess — twice, because of the documented
first-run ordering gotcha — and asserts the generated artifacts. This is the
same shape every real C++ port (e.g. a robot client) will use.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from guardrail_hub.installer import apply_kit

ARCHITECTURE = """\
[project]
root_package = "agent"
src_layout = "src"
language = "cpp"

[component_docs]
App = "The composition root."
Core = "The engine."
Util = "Leaf helpers."
Gen = "Generated protobuf headers (build dir)."

[components]
App = ["agent.app"]
Core = ["agent.core"]
Util = ["agent.util"]
Gen = ["agent.gen"]

[tiers]
App = ["App"]
Core = ["Core"]
Foundation = ["Util", "Gen"]

[budgets]
cross_component_edges = 4
component_cycles = 0
module_cycles = 0
modules_over_800_lines = 0
max_module_lines = 800

[domain]
include_modules = []
exclude_modules = []

[cpp.virtual_includes]
"gen/" = "gen"
"""

SOURCES = {
    "src/util/log.h": "#pragma once\nvoid log_line(const char* msg);\n",
    "src/core/engine.h": '#pragma once\n#include "util/log.h"\nstruct Engine { int run(); };\n',
    "src/core/engine.cpp": (
        '#include "core/engine.h"\n'
        '#include "gen/api.pb.h"\n'
        "// block comment lines are counted as sloc (accepted approximation)\n"
        "int Engine::run() { return 0; }\n"
    ),
    "src/app/main.cpp": (
        '#include "core/engine.h"\n#include "util/log.h"\nint main() { return Engine{}.run(); }\n'
    ),
}


@pytest.fixture(scope="module")
def cpp_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("cppkit") / "agent"
    (target / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=True)
    for rel, content in SOURCES.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    apply_kit(target, language="cpp", root_package="agent")
    # replace the installer's TODO skeleton with a real, resolved config
    (target / "docs" / "architecture.toml").write_text(ARCHITECTURE, encoding="utf-8")
    (target / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    # The FIRST run must pass even though docs/generated/ starts empty —
    # conftest.py generates every artifact before validation (this is the
    # regression guard for the old run-twice ordering gotcha).
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/guardrail", "-q", "-p", "no:cacheprovider"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return target


def test_component_diagram_shows_include_edges_and_virtual_seam(cpp_project: Path) -> None:
    diagram = (cpp_project / "docs" / "generated" / "architecture.md").read_text(encoding="utf-8")

    assert "graph TD" in diagram
    assert "App --> Core" in diagram
    assert "Core --> Gen" in diagram  # via [cpp.virtual_includes]
    assert "Core --> Util" in diagram


def test_metrics_merge_pairs_and_language_sections(cpp_project: Path) -> None:
    metrics = json.loads(
        (cpp_project / "docs" / "generated" / "metrics.json").read_text(encoding="utf-8")
    )

    # engine.h + engine.cpp merge into one module; log.h and main.cpp are one each
    assert metrics["size"]["total_modules"] == 3
    assert metrics["graph"]["cross_component_edges"] == 4
    # every module-level edge is unique here, so the weighted count matches
    assert metrics["graph"]["cross_component_module_edges"] == 4
    assert metrics["domain"]["models"] == 0
    assert metrics["size"]["components"]["Core"]["modules"] == 1
    # complexity comes from lizard for cpp: Engine::run + main are the only
    # function DEFINITIONS (log.h holds a declaration, which lizard skips)
    assert metrics["complexity"]["total_functions"] == 2
    assert metrics["complexity"]["functions_over_10"] == 0
    assert metrics["complexity"]["components"]["Core"]["max_function"] == (
        "agent.core.engine.Engine::run"
    )


def test_domain_model_artifact_is_omitted_for_cpp(cpp_project: Path) -> None:
    generated = cpp_project / "docs" / "generated"

    assert not (generated / "domain_model.md").exists()
    assert (generated / "README.md").is_file()
    index = (generated / "README.md").read_text(encoding="utf-8")
    assert "domain_model.md" not in index
    assert "test_layering_contract.py" in index  # the cpp enforcement legend


def test_generated_prose_uses_declared_tier_names(cpp_project: Path) -> None:
    """Index/metrics prose must speak the repo's own [tiers] vocabulary."""
    generated = cpp_project / "docs" / "generated"
    index = (generated / "README.md").read_text(encoding="utf-8")
    metrics_md = (generated / "metrics.md").read_text(encoding="utf-8")

    assert "grouped by tier (App → Core → Foundation)" in index
    assert "Tier subgraphs (App / Core / Foundation)" in index
    assert "Tier-skipping edges (App → Foundation)" in metrics_md
    assert "Entrypoints" not in index and "Entrypoints" not in metrics_md


def test_component_page_lists_merged_module(cpp_project: Path) -> None:
    page = (cpp_project / "docs" / "generated" / "components" / "Core.md").read_text(
        encoding="utf-8"
    )

    assert "`agent.core.engine`" in page
    assert "## Public API" in page  # section header present, no Python API entries
    assert "### `agent.core.engine`" not in page


def test_upward_include_edge_fails_layering_contract(cpp_project: Path) -> None:
    offender = cpp_project / "src" / "util" / "bad.h"
    offender.write_text('#pragma once\n#include "core/engine.h"\n', encoding="utf-8")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/guardrail/test_layering_contract.py",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            cwd=cpp_project,
            capture_output=True,
            text=True,
        )
    finally:
        offender.unlink()

    assert result.returncode != 0
    assert "Util -> Core" in result.stdout
