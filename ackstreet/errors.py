"""Exception taxonomy for ACKSTREET AGENT.

Every failure the agent can hit maps to one of these so the CLI can print a
useful message instead of a traceback.
"""

from __future__ import annotations


class AckstreetError(Exception):
    """Base class for all ACKSTREET AGENT errors."""


class ConfigError(AckstreetError):
    """Malformed or missing configuration."""


class ProviderError(AckstreetError):
    """The LLM backend could not be reached or returned an error."""

    def __init__(self, message: str, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ToolError(AckstreetError):
    """A tool failed in an expected, reportable way."""


class SkillError(AckstreetError):
    """A skill file is malformed or a skill operation failed."""


class StoreError(AckstreetError):
    """Persistent memory or session storage failed."""


__all__ = [
    "AckstreetError",
    "ConfigError",
    "ProviderError",
    "ToolError",
    "SkillError",
    "StoreError",
]
