"""Chat-platform connectors.

Importing this package is cheap and safe: the bundled connectors are only
imported when :func:`load_builtin` is called, so a base install with neither
``[telegram]`` nor ``[whatsapp]`` extras still imports cleanly.
"""

from __future__ import annotations

from .base import (
    DEFAULT_CHUNK_LIMIT,
    Connector,
    ConnectorError,
    IncomingMessage,
    MessageHandler,
    NotInstalledError,
    OutgoingMessage,
    chunk_text,
)
from .registry import build, find, get, load_builtin, names, register
from .router import MessageRouter, RouteOutcome, classify_answer
from .sessions import ChatSession, SessionManager

__all__ = [
    "DEFAULT_CHUNK_LIMIT",
    "ChatSession",
    "Connector",
    "ConnectorError",
    "IncomingMessage",
    "MessageHandler",
    "MessageRouter",
    "NotInstalledError",
    "OutgoingMessage",
    "RouteOutcome",
    "SessionManager",
    "build",
    "chunk_text",
    "classify_answer",
    "find",
    "get",
    "load_builtin",
    "names",
    "register",
]
