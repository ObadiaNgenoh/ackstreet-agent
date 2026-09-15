"""Platform-agnostic connector layer.

A *connector* is the bridge between a chat platform (Telegram, WhatsApp, and
whatever comes next) and the agent. Everything platform-specific lives in a
subclass; everything shared -- the user allowlist, per-chat sessions, and
routing incoming text through the agent loop and its approval gate -- lives in
:mod:`ackstreet.connectors.router`.

Adding a third platform means writing one :class:`Connector` subclass and
registering it; the agent core is not touched.

Design constraints worth knowing:

* A connector only moves *text* in and out. It never calls the agent directly,
  so it cannot bypass the approval gate.
* Credentials live in the config file under ``connectors.<name>`` and can be
  overridden by environment variables, so a VM or container can be configured
  without editing the file.
* :meth:`Connector.listen` blocks and hands each message to a callback. Long
  polling is used where the platform supports it, so no inbound port, public
  URL or webhook is needed -- which is what makes this work on a home VM
  behind NAT.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..config import Config

#: Platforms cap message length; answers are split before sending.
DEFAULT_CHUNK_LIMIT = 4000


def chunk_text(text: str, limit: int = DEFAULT_CHUNK_LIMIT) -> List[str]:
    """Split *text* into pieces of at most *limit* characters.

    Prefers to break on a newline, then on a space, and only falls back to a
    hard cut when a single token is longer than the limit. Empty input yields
    an empty list so callers never send a blank message.
    """
    if not text:
        return []
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        piece = remaining[:cut].rstrip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].lstrip("\n")
    if remaining.strip():
        chunks.append(remaining)
    return chunks


@dataclass
class IncomingMessage:
    """One message received from a chat platform, normalised."""

    platform: str
    chat_id: str
    user_id: str
    text: str
    message_id: str = ""
    chat_type: str = "private"  # "private" | "group"
    user_name: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        """Key identifying the conversation this message belongs to."""
        return f"{self.platform}:{self.chat_id}"

    def summary(self) -> str:
        body = self.text.strip().replace("\n", " ")
        if len(body) > 80:
            body = body[:77] + "..."
        return f"{self.platform}/{self.chat_id} <- {self.user_id}: {body}"


@dataclass
class OutgoingMessage:
    """A message to deliver to a chat."""

    chat_id: str
    text: str
    reply_to: Optional[str] = None


#: Callback invoked for every accepted inbound message.
MessageHandler = Callable[[IncomingMessage], None]


class ConnectorError(Exception):
    """Raised for connector setup and transport failures."""


class NotInstalledError(ConnectorError):
    """Raised when the optional dependency for a connector is missing."""

    def __init__(self, connector: str, packages: Sequence[str], extra: str) -> None:
        self.connector = connector
        self.packages = list(packages)
        self.extra = extra
        noun = "packages" if len(self.packages) > 1 else "package"
        super().__init__(
            f"the {connector} connector needs the {noun} "
            f"{', '.join(packages)}, which {'are' if len(self.packages) > 1 else 'is'} "
            "not installed.\n"
            "Install " + ("them" if len(self.packages) > 1 else "it") +
            " with:  pip install 'ackstreet-agent[" + str(extra) + "]'"
        )


class Connector(abc.ABC):
    """Base class for every chat-platform bridge."""

    #: Stable identifier used in config keys and CLI commands.
    name: str = ""
    #: Human-readable label for docs and `doctor` output.
    display_name: str = ""
    #: The pip extra that installs this connector's optional dependencies.
    extra: str = ""
    #: True when the platform lets us rewrite an already-sent message.
    supports_edit: bool = False
    #: Maximum characters per outbound message.
    chunk_limit: int = DEFAULT_CHUNK_LIMIT

    def __init__(
        self, config: Config, on_message: Optional[MessageHandler] = None
    ) -> None:
        self.config = config
        self.on_message = on_message
        self._stop_event = threading.Event()
        self._log: List[str] = []

    # -- credentials -------------------------------------------------------

    @classmethod
    def credential_env_vars(cls) -> Tuple[str, ...]:
        """Environment variables, highest priority first, that supply the key."""
        return ()

    @classmethod
    def is_configured(cls, config: Config) -> bool:
        """True when the connector has everything it needs to run."""
        raise NotImplementedError

    @classmethod
    def store_credentials(cls, config: Config, **values: Any) -> Path:
        """Persist credentials into the config file and save it."""
        raise NotImplementedError

    @classmethod
    def settings(cls, config: Config) -> Dict[str, Any]:
        """The ``connectors.<name>`` table, or an empty dict."""
        raw = config.get("connectors", cls.name, {})
        return dict(raw) if isinstance(raw, dict) else {}

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings(self.config).get(key, default)

    # -- authorisation -----------------------------------------------------

    def authorized_user_ids(self) -> List[str]:
        """The configured allowlist, normalised to a list of strings.

        Shared ``connectors.allowed_user_ids`` applies to every platform; a
        platform-specific list is merged on top of it.
        """
        values: List[str] = []
        shared = self.config.get("connectors", "allowed_user_ids", []) or []
        if isinstance(shared, (list, tuple)):
            values.extend(str(v).strip() for v in shared if str(v).strip())
        own = self.setting("allowed_user_ids", []) or []
        if isinstance(own, (list, tuple)):
            values.extend(str(v).strip() for v in own if str(v).strip())
        # De-duplicate while preserving order.
        seen: Dict[str, None] = {}
        for value in values:
            seen.setdefault(value, None)
        return list(seen)

    def allow_group_chats(self) -> bool:
        """Whether the bot will answer in group chats (default: no)."""
        shared = self.config.get("connectors", "allow_group_chats", None)
        value = self.setting("allow_group_chats", shared if shared is not None else False)
        return bool(value)

    @staticmethod
    def _bare_id(value: str) -> str:
        """Strip a WhatsApp-style suffix (``@s.whatsapp.net``, ``:12``)."""
        return value.split("@", 1)[0].split(":", 1)[0].strip()

    def authorize(self, message: IncomingMessage) -> Tuple[bool, str]:
        """Decide whether this user may drive the agent.

        An empty allowlist allows everyone, which is convenient for a personal
        bot and dangerous for a published one -- ``ackstreet doctor`` warns
        about it. A non-empty allowlist is an exact match on the sender id;
        ``*`` is the explicit "allow everyone" escape hatch.
        """
        if message.chat_type != "private" and not self.allow_group_chats():
            return False, (
                "this bot is configured to ignore group chats "
                f"(set connectors.{self.name}.allow_group_chats = true to enable)"
            )

        allowed = self.authorized_user_ids()
        if not allowed:
            return True, "allowlist is empty: every user who can reach the bot may use it"
        if "*" in allowed:
            return True, "allowlist contains '*': every user is allowed"

        sender = str(message.user_id).strip()
        if sender in allowed:
            return True, f"user {sender} is allowlisted"

        allowed_bare = {self._bare_id(entry) for entry in allowed}
        bare = self._bare_id(sender)
        if bare and bare in allowed_bare:
            return True, f"user {sender} matches allowlist entry {bare}"

        return False, (
            f"user {sender} is not on the allowlist, so this bot ignored the message. "
            f"Add the id to connectors.{self.name}.allowed_user_ids to grant access."
        )

    # -- messaging ---------------------------------------------------------

    @abc.abstractmethod
    def send(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> Optional[str]:
        """Deliver one message. Returns the platform message id, if any."""

    def send_long(
        self, chat_id: str, text: str, reply_to: Optional[str] = None
    ) -> List[str]:
        """Send *text*, splitting it across messages when it is too long."""
        ids: List[str] = []
        for index, piece in enumerate(chunk_text(text, self.chunk_limit)):
            ids.append(self.send(chat_id, piece, reply_to if index == 0 else None) or "")
        return ids

    def send_typing(self, chat_id: str) -> None:
        """Show a typing indicator, where the platform supports one."""
        return None

    def edit(self, chat_id: str, message_id: str, text: str) -> bool:
        """Rewrite a sent message. Returns False when unsupported."""
        return False

    # -- lifecycle ---------------------------------------------------------

    @abc.abstractmethod
    def listen(
        self, on_message: Optional[MessageHandler] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Block, delivering inbound messages until stopped."""

    def stop(self) -> None:
        """Ask the listen loop to finish after the current poll."""
        self._stop_event.set()

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    def close(self) -> None:
        self.stop()

    def describe(self) -> str:
        state = "configured" if self.is_configured(self.config) else "not configured"
        return f"{self.name} ({state})"


__all__ = [
    "DEFAULT_CHUNK_LIMIT",
    "Connector",
    "ConnectorError",
    "IncomingMessage",
    "MessageHandler",
    "NotInstalledError",
    "OutgoingMessage",
    "chunk_text",
]
