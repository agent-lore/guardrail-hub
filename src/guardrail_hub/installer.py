"""Kit installer: port the canonical kit into a target repo. (Arrives fully in phase 4.)"""

from __future__ import annotations

from pathlib import Path

from guardrail_hub.errors import KitError


def apply_kit(
    target: Path,
    *,
    root_package: str | None = None,
    with_tool_catalog: bool = False,
    with_containers: bool = False,
) -> str:
    raise KitError("guardrail-hub apply is not implemented yet")
