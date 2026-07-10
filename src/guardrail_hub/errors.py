"""Exception hierarchy for guardrail-hub.

Everything raised on purpose derives from ``GuardrailHubError`` so entry points
can catch one type, print a clean message, and exit non-zero.
"""

from __future__ import annotations


class GuardrailHubError(Exception):
    """Base class for all guardrail-hub errors."""


class ConfigError(GuardrailHubError):
    """The hub config file is missing, malformed, or fails validation."""


class RepoAccessError(GuardrailHubError):
    """A registered repo could not be read (missing dir, not a git repo, git failure)."""


class KitError(GuardrailHubError):
    """The canonical kit is malformed or an installer operation cannot proceed."""
