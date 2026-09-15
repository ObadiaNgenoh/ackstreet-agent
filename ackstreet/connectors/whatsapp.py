"""WhatsApp connector (WhatsApp Web multi-device, via neonize).

WhatsApp has no bot API for personal accounts. The supported route is the
WhatsApp Web *multi-device* protocol, which is what `whatsmeow` implements and
what `neonize` binds to Python. Authentication is a QR code: the user scans it
once from their phone and the session is then persisted on disk, so restarts do
not require re-scanning.

Because the protocol is unofficial, this connector is explicitly
**use-at-your-own-risk** and is kept isolated behind an optional dependency:

    pip install 'ackstreet-agent[whatsapp]'

Everything except the neonize client itself is testable without the library:
the client is injected, so the unit tests drive the connector with a fake.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..config import Config
from .base import (
    Connector,
    ConnectorError,
    IncomingMessage,
    MessageHandler,
    NotInstalledError,
)
from .registry import register

#: Package that provides the WhatsApp Web multi-device client.
NEONIZE_PACKAGES = ("neonize",)
#: Package used to render the QR code in a terminal.
QR_PACKAGES = ("qrcode",)


class WhatsAppDependencyError(NotInstalledError):
    """Raised when neonize (or the QR renderer) is missing."""


def render_qr_terminal(payload: str) -> str:
    """Render a QR payload as text for the terminal.

    Uses the optional ``qrcode`` package when present. Without it, the raw
    payload is returned with instructions, so the user can still complete the
    scan with any QR generator rather than being stuck.
    """
    if not payload:
        return "(no QR payload received)"
    try:
        import qrcode  # noqa: PLC0415 - optional dependency
    except ImportError:
        return (
            "Install 'qrcode' to draw this in the terminal:\n"
            "  pip install 'ackstreet-agent[whatsapp]'\n"
            "Otherwise paste this payload into any QR generator and scan it:\n"
            f"{payload}"
        )

    qr = qrcode.QRCode(border=1)
    qr.add_data(payload)
    qr.make(fit=True)
    printer = getattr(qr, "print_ascii", None)
    if callable(printer):
        try:
            import io

            buffer = io.StringIO()
            printer(out=buffer, invert=True)
            return buffer.getvalue()
        except Exception:  # noqa: BLE001 - fall back to the payload
            pass

    import io

    buffer = io.StringIO()
    try:
        qr.print_tty(out=buffer)  # type: ignore[attr-defined]
        return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return f"Scan this QR payload with WhatsApp:\n{payload}"


@register
class WhatsAppConnector(Connector):
    """Talk to the agent from WhatsApp, via the multi-device protocol."""

    name = "whatsapp"
    display_name = "WhatsApp"
    extra = "whatsapp"
    supports_edit = False
    chunk_limit = 4000

    def __init__(
        self,
        config: Config,
        on_message: Optional[MessageHandler] = None,
        client: Any = None,
        qr_renderer=None,
        event_types: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(config, on_message=on_message)
        self._client = client
        self._event_types = event_types
        self.qr_renderer = qr_renderer or render_qr_terminal
        self.connected = threading.Event()
        self.logged_in_as = ""
        self.last_qr = ""
        self._handlers: Dict[str, Any] = {}

    # -- credentials / session --------------------------------------------

    @classmethod
    def credential_env_vars(cls) -> Tuple[str, ...]:
        return ("ACKSTREET_WHATSAPP_SESSION",)

    @property
    def session_path(self) -> Path:
        """Where the multi-device session (and login) is persisted.

        Defaults inside the ACKSTREET home so the login survives restarts and
        is covered by whatever backs that directory up.
        """
        import os

        override = os.environ.get("ACKSTREET_WHATSAPP_SESSION")
        raw = override or self.setting("session_path", "")
        if raw:
            return Path(str(raw)).expanduser()
        return self.config.root / "whatsapp" / "session.db"

    @classmethod
    def is_configured(cls, config: Config) -> bool:
        """True once a session file exists -- i.e. the QR was scanned.

        Before that there is nothing to configure: the QR scan *is* the setup.
        """
        import os

        override = os.environ.get("ACKSTREET_WHATSAPP_SESSION")
        raw = override or cls.settings(config).get("session_path", "")
        path = Path(str(raw)).expanduser() if raw else config.root / "whatsapp" / "session.db"
        return path.exists()

    @classmethod
    def store_credentials(cls, config: Config, session_path: str = "", **_: Any):
        settings = dict(cls.settings(config))
        if session_path:
            settings["session_path"] = str(session_path)
        config.set("connectors", cls.name, settings)
        return config.save()

    # -- optional dependency ----------------------------------------------

    def _require_neonize(self) -> Any:
        """Import neonize, or explain precisely how to install it."""
        try:
            import neonize  # noqa: PLC0415 - optional dependency
        except ImportError as exc:
            raise WhatsAppDependencyError(
                "whatsapp", NEONIZE_PACKAGES, "whatsapp"
            ) from exc
        return neonize

    def _resolve_event_types(self) -> Dict[str, Any]:
        """The neonize event classes we subscribe to.

        Injected in tests; imported lazily at runtime.
        """
        if self._event_types is not None:
            return self._event_types
        self._require_neonize()
        from neonize.events import (  # noqa: PLC0415 - optional dependency
            ConnectedEv,
            MessageEv,
            QREv,
        )

        return {"qr": QREv, "message": MessageEv, "connected": ConnectedEv}

    @property
    def client(self) -> Any:
        """The neonize client, created on first use."""
        if self._client is None:
            self._require_neonize()
            from neonize.client import NewClient  # noqa: PLC0415

            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self._client = NewClient(str(self.session_path))
        return self._client

    # -- QR -----------------------------------------------------------------

    def handle_qr(self, payload: Any) -> str:
        """Render and remember a QR payload for the user to scan."""
        text = str(payload or "")
        self.last_qr = text
        rendered = self.qr_renderer(text)
        self._log.append("QR code issued")
        print()
        print("Scan this QR code with WhatsApp on your phone:")
        print("  WhatsApp > Settings > Linked devices > Link a device")
        print()
        print(rendered)
        print()
        return rendered

    def print_setup_instructions(self) -> None:
        print("To connect WhatsApp:")
        print("  1. Open WhatsApp on your phone.")
        print("  2. Go to Settings > Linked devices > Link a device.")
        print("  3. Scan the QR code shown below.")
        print(f"  4. The login is saved to {self.session_path} and survives restarts.")
        print()

    # -- messaging ---------------------------------------------------------

    @staticmethod
    def _normalise_recipient(chat_id: str) -> str:
        """Turn a bare number into a JID neonize will accept."""
        value = str(chat_id).strip()
        if "@" in value:
            return value
        return f"{value}@s.whatsapp.net"

    def send(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> Optional[str]:
        recipient = self._normalise_recipient(chat_id)
        try:
            result = self.client.send_message(recipient, text)
        except WhatsAppDependencyError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as a connector error
            raise ConnectorError(f"WhatsApp send failed for {recipient}: {exc}") from exc

        for attribute in ("ID", "id", "message_id"):
            value = getattr(result, attribute, None)
            if value:
                return str(value)
        return None

    # -- parsing -----------------------------------------------------------

    @staticmethod
    def extract_text(payload: Any) -> str:
        """Pull plain text out of a neonize message payload.

        Handles a protobuf-style object (attribute access) as well as a plain
        dict, and returns "" when the message carries no text (media, receipts).
        """
        message = payload
        for attribute in ("Message", "message"):
            candidate = getattr(payload, attribute, None)
            if candidate is not None:
                message = candidate
                break
        if isinstance(payload, dict):
            message = payload.get("Message", payload.get("message", payload))

        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            for key in ("conversation", "extendedTextMessage", "text"):
                value = message.get(key)
                if isinstance(value, str):
                    return value
                if isinstance(value, dict) and isinstance(value.get("text"), str):
                    return value["text"]
            return ""

        for attribute in ("conversation", "extendedTextMessage"):
            value = getattr(message, attribute, None)
            if isinstance(value, str):
                return value
            nested = getattr(value, "text", None)
            if isinstance(nested, str):
                return nested
        return ""

    def parse_event(self, payload: Any) -> Optional[IncomingMessage]:
        """Turn one neonize message event into an :class:`IncomingMessage`."""
        text = self.extract_text(payload)
        if not text.strip():
            return None

        info = getattr(payload, "Info", None) or getattr(payload, "info", None)
        if info is None and isinstance(payload, dict):
            info = payload.get("Info") or payload.get("info") or {}

        def field(name: str, *aliases: str) -> Any:
            for key in (name, *aliases):
                if isinstance(info, dict) and key in info:
                    return info[key]
                value = getattr(info, key, None)
                if value is not None:
                    return value
            return None

        chat = str(field("Chat", "chat") or "")
        sender = str(field("Sender", "sender") or "")
        sender_alt = str(field("SenderAlt", "sender_alt") or "")
        message_id = str(field("ID", "id", "message_id") or "")
        is_group = bool(field("IsGroup", "is_group") or "@g.us" in chat)

        if not chat:
            return None

        # In a group, SenderAlt is the participant rather than the group.
        user_id = sender_alt or sender or chat

        return IncomingMessage(
            platform="whatsapp",
            chat_id=chat,
            user_id=user_id,
            text=text,
            message_id=message_id,
            chat_type="group" if is_group else "private",
            user_name=str(field("PushName", "push_name") or ""),
            raw={"payload": payload},
        )

    # -- lifecycle ---------------------------------------------------------

    def wire_events(self, client: Any, handler: MessageHandler) -> Dict[str, Any]:
        """Attach our QR / connected / message handlers to *client*.

        Supports both the decorator API (``@client.event(EventType)``) and a
        plain ``add_event_handler`` call, so a fake client in tests only has to
        implement one of them.
        """
        types = self._resolve_event_types()

        def on_qr(_client: Any, payload: Any = None, *_args: Any) -> None:
            self.handle_qr(payload)

        def on_connected(_client: Any, *_args: Any) -> None:
            self.connected.set()
            self._log.append("connected")
            print("WhatsApp connected. The agent is now listening for messages.")

        def on_message(_client: Any, payload: Any, *_args: Any) -> None:
            incoming = self.parse_event(payload)
            if incoming is None:
                return
            try:
                handler(incoming)
            except Exception as exc:  # noqa: BLE001 - keep the socket alive
                self._log.append(f"handler failed: {exc}")

        self._handlers = {"qr": on_qr, "connected": on_connected, "message": on_message}

        decorator = getattr(client, "event", None)
        if callable(decorator):
            for key, callback in self._handlers.items():
                if key not in types:
                    continue
                decorator(types[key])(callback)
        else:
            adder = getattr(client, "add_event_handler", None)
            if not callable(adder):
                raise ConnectorError(
                    "the WhatsApp client exposes neither event() nor "
                    "add_event_handler(); it is not a neonize client"
                )
            for key, callback in self._handlers.items():
                if key in types:
                    adder(types[key], callback)

        return self._handlers

    def listen(
        self,
        on_message: Optional[MessageHandler] = None,
        stop_event=None,
    ) -> None:
        """Connect, then block until stopped.

        neonize drives its own asyncio loop, so this method wires handlers and
        calls ``connect()``; the surrounding loop only watches for stop
        requests.
        """
        handler = on_message or self.on_message
        if handler is None:
            raise ConnectorError("no message handler supplied to listen()")

        client = self.client
        self.wire_events(client, handler)

        self.print_setup_instructions()
        self.session_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            client.connect()
        except WhatsAppDependencyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"WhatsApp connection failed: {exc}") from exc

        while not self.stopping and not (stop_event is not None and stop_event.is_set()):
            time.sleep(0.2)

    def close(self) -> None:
        super().close()
        if self._client is not None:
            for name in ("disconnect", "close", "stop"):
                method = getattr(self._client, name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:  # noqa: BLE001
                        pass
                    break

    def describe(self) -> str:
        state = "session found" if self.is_configured(self.config) else "not linked"
        return f"whatsapp ({state}, session at {self.session_path})"


__all__ = [
    "NEONIZE_PACKAGES",
    "QR_PACKAGES",
    "WhatsAppConnector",
    "WhatsAppDependencyError",
    "render_qr_terminal",
]
