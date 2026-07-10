"""FastAPI dashboard. (Arrives fully in phase 5.)"""

from __future__ import annotations

from pathlib import Path

from guardrail_hub.errors import GuardrailHubError


def serve(config_path: Path | None = None, host: str | None = None, port: int | None = None) -> int:
    raise GuardrailHubError("guardrail-hub serve is not implemented yet")
