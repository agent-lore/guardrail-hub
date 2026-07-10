"""Logging configuration for guardrail-hub."""

from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "info") -> None:
    """Configure root logging once, idempotently."""
    logging.basicConfig(level=level.upper(), format=_FORMAT)
