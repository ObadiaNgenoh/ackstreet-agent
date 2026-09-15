"""Connector registry.

The registry is what makes the platform list pluggable: a connector registers
itself by subclassing :class:`~ackstreet.connectors.base.Connector` and
decorating the class with :func:`register`. The CLI, the router and `doctor`
all discover connectors through here, so adding a platform never means editing
the agent core.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type

from .base import Connector, ConnectorError

_REGISTRY: Dict[str, Type[Connector]] = {}


def register(cls: Type[Connector]) -> Type[Connector]:
    """Class decorator: add a connector to the registry."""
    if not getattr(cls, "name", ""):
        raise ConnectorError(f"{cls.__name__} must define a non-empty `name`")
    _REGISTRY[cls.name] = cls
    return cls


def names() -> List[str]:
    """Every registered connector name, sorted."""
    return sorted(_REGISTRY)


def get(name: str) -> Type[Connector]:
    """Look up a connector class by name."""
    key = (name or "").strip().lower()
    if key not in _REGISTRY:
        known = ", ".join(names()) or "(none)"
        raise ConnectorError(f"unknown connector '{name}'. Known connectors: {known}")
    return _REGISTRY[key]


def find(name: str) -> Optional[Type[Connector]]:
    """Like :func:`get` but returns None instead of raising."""
    return _REGISTRY.get((name or "").strip().lower())


def build(name: str, config, on_message=None) -> Connector:
    """Instantiate a registered connector."""
    return get(name)(config, on_message=on_message)


def load_builtin() -> None:
    """Import the bundled connectors so they self-register.

    The imports are deferred to call time so that merely importing
    :mod:`ackstreet.connectors` never pulls in an optional dependency.
    """
    from . import telegram, whatsapp  # noqa: F401  (import for side effect)


__all__ = ["build", "find", "get", "load_builtin", "names", "register"]
