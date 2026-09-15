"""Telegram connector (Bot API, long polling).

The Telegram Bot API supports *long polling* via ``getUpdates``, so the bot
needs no inbound port, no public URL and no TLS certificate. It works from a
home VM behind NAT, which is exactly the deployment this project targets.

Setup, in short (full steps in the README::

1. open Telegram, talk to **@BotFather**, send ``/newbot`` and follow the
   prompts; BotFather replies with a token like ``123456:ABC-DEF...``;
2. ``ackstreet connect telegram --token <token>``
3. ``ackstreet serve telegram``

The HTTP layer is a ``Transport`` object so tests can drive the connector with
recorded responses instead of a live Telegram server.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

from ..config import Config
from .base import (
    Connector,
    ConnectorError,
    IncomingMessage,
    MessageHandler,
    NotInstalledError,
)
from .registry import register

API_ROOT = "https://api.telegram.org"


class TelegramTransport:
    """Sends a Bot API method call and returns the decoded ``result``."""

    def call(self, method: str, payload: Dict[str, Any]) -> Any:
        raise NotImplementedError

    def close(self) -> None:
        return None


class HttpTelegramTransport(TelegramTransport):
    """Real transport: HTTPS to ``api.telegram.org``.

    ``httpx`` is a core dependency, so Telegram needs no extra package -- the
    ``[telegram]`` extra only exists to document the feature.
    """

    def __init__(
        self,
        token: str,
        base_url: str = API_ROOT,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def call(self, method: str, payload: Dict[str, Any]) -> Any:
        if not self.token:
            raise ConnectorError(
                "no Telegram bot token configured. "
                "Run `ackstreet connect telegram --token <token>` first."
            )
        url = f"{self.base_url}/bot{self.token}/{method}"
        # Long polling needs a read timeout longer than the server-side wait.
        request_timeout = self.timeout
        if method == "getUpdates":
            request_timeout = float(payload.get("timeout", 25)) + 15.0
        try:
            response = self.client.post(url, json=payload, timeout=request_timeout)
        except httpx.TimeoutException as exc:
            raise ConnectorError(f"Telegram request timed out ({method}): {exc}") from exc
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Telegram request failed ({method}): {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ConnectorError(
                f"Telegram returned a non-JSON response ({response.status_code}): "
                f"{response.text[:200]}"
            ) from exc

        if not isinstance(body, dict):
            raise ConnectorError(f"unexpected Telegram response shape: {body!r}")

        if not body.get("ok"):
            code = body.get("error_code")
            description = body.get("description") or "unknown error"
            if code == 401:
                raise ConnectorError(
                    f"Telegram rejected the bot token (401: {description}). "
                    "Re-run `ackstreet connect telegram --token <token>` with a "
                    "current token from @BotFather."
                )
            if code == 409:
                raise ConnectorError(
                    "Telegram says another process is already polling this bot "
                    "(409 Conflict). Stop the other copy, or use a webhook instead "
                    "of long polling. Only one getUpdates consumer is allowed."
                )
            raise ConnectorError(f"Telegram error {code}: {description}")

        return body.get("result")

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


@register
class TelegramConnector(Connector):
    """Talk to the agent from Telegram."""

    name = "telegram"
    display_name = "Telegram"
    extra = "telegram"
    supports_edit = True
    chunk_limit = 4000

    def __init__(
        self,
        config: Config,
        on_message: Optional[MessageHandler] = None,
        transport: Optional[TelegramTransport] = None,
        poll_timeout: int = 25,
    ) -> None:
        super().__init__(config, on_message=on_message)
        self._transport = transport
        self.poll_timeout = int(poll_timeout)
        self.offset = int(self.setting("update_offset", 0) or 0)
        self.bot_username = ""

    # -- credentials -------------------------------------------------------

    @classmethod
    def credential_env_vars(cls) -> Tuple[str, ...]:
        return ("ACKSTREET_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")

    @property
    def token(self) -> str:
        import os

        for var in self.credential_env_vars():
            value = os.environ.get(var)
            if value:
                return value.strip()
        return str(self.setting("bot_token", "") or "").strip()

    @classmethod
    def is_configured(cls, config: Config) -> bool:
        import os

        for var in cls.credential_env_vars():
            if os.environ.get(var):
                return True
        settings = cls.settings(config)
        return bool(str(settings.get("bot_token", "") or "").strip())

    @classmethod
    def store_credentials(cls, config: Config, token: str = "", **_: Any):
        settings = dict(cls.settings(config))
        if token:
            settings["bot_token"] = token.strip()
        config.set("connectors", cls.name, settings)
        return config.save()

    @property
    def transport(self) -> TelegramTransport:
        if self._transport is None:
            self._transport = HttpTelegramTransport(self.token)
        return self._transport

    # -- Bot API calls -----------------------------------------------------

    def call(self, method: str, **payload: Any) -> Any:
        return self.transport.call(method, payload)

    def get_me(self) -> Dict[str, Any]:
        """Verify the token and learn the bot's own username."""
        result = self.call("getMe")
        if isinstance(result, dict):
            self.bot_username = str(result.get("username", ""))
            return result
        raise ConnectorError("Telegram getMe returned an unexpected payload")

    def get_updates(self, offset: Optional[int] = None) -> List[Dict[str, Any]]:
        """Long-poll for new updates."""
        payload = {
            "timeout": self.poll_timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = int(offset)
        result = self.call("getUpdates", **payload)
        if result is None:
            return []
        if not isinstance(result, list):
            raise ConnectorError(f"getUpdates returned {type(result).__name__}, expected a list")
        return [item for item in result if isinstance(item, dict)]

    # -- messaging ---------------------------------------------------------

    def send(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> Optional[str]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            # Plain text: the agent's output may contain characters Telegram
            # would otherwise reject as broken Markdown, and partial delivery
            # is worse than no formatting.
            "disable_web_page_preview": True,
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        result = self.call("sendMessage", **payload)
        if isinstance(result, dict):
            return str(result.get("message_id", ""))
        return None

    def send_typing(self, chat_id: str) -> None:
        try:
            self.call("sendChatAction", chat_id=chat_id, action="typing")
        except ConnectorError:
            pass

    def edit(self, chat_id: str, message_id: str, text: str) -> bool:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
        except ConnectorError:
            return False
        return True

    # -- parsing -----------------------------------------------------------

    @staticmethod
    def parse_update(update: Dict[str, Any]) -> Optional[IncomingMessage]:
        """Turn one Telegram update into an :class:`IncomingMessage`.

        Returns None for updates that carry nothing to act on (photos, join
        events, edits) so the router is never handed an empty instruction.
        """
        message = update.get("message")
        if not isinstance(message, dict):
            return None

        text = message.get("text")
        if text is None:
            caption = message.get("caption")
            text = caption if isinstance(caption, str) else None
        if not text or not str(text).strip():
            return None

        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return None

        chat_type_raw = str(chat.get("type", "private"))
        chat_type = "group" if chat_type_raw in ("group", "supergroup") else "private"

        name_parts = [sender.get("first_name") or "", sender.get("last_name") or ""]
        display = " ".join(p for p in name_parts if p).strip()
        if not display:
            display = str(sender.get("username") or "")

        return IncomingMessage(
            platform="telegram",
            chat_id=str(chat_id),
            user_id=str(sender.get("id", chat_id)),
            text=str(text),
            message_id=str(message.get("message_id", "")),
            chat_type=chat_type,
            user_name=display,
            raw=update,
        )

    # -- lifecycle ---------------------------------------------------------

    def listen(
        self,
        on_message: Optional[MessageHandler] = None,
        stop_event=None,
    ) -> None:
        """Long-poll until stopped, calling the handler for each message.

        Note that a stop requested *before* ``listen`` is honoured rather than
        cleared: the loop is meant to be run once per connector instance.
        """
        handler = on_message or self.on_message
        backoff = 1.0

        while not self.stopping and not (stop_event is not None and stop_event.is_set()):
            try:
                updates = self.get_updates(self.offset)
                backoff = 1.0
            except NotInstalledError:
                raise
            except ConnectorError as exc:
                self._log.append(f"poll failed: {exc}")
                # A 409 means the token is healthy but someone else is polling;
                # retrying in a tight loop would make that worse.
                time.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self.offset = max(self.offset, update_id + 1)
                incoming = self.parse_update(update)
                if incoming is None or handler is None:
                    continue
                try:
                    handler(incoming)
                except Exception as exc:  # noqa: BLE001 - one message must not stop the bot
                    self._log.append(f"handler failed: {exc}")

            if not updates:
                # getUpdates returned early (short poll timeout); yield briefly
                # so a stop request is noticed promptly and we do not spin.
                time.sleep(0.05)

    def close(self) -> None:
        super().close()
        if self._transport is not None:
            self._transport.close()

    def describe(self) -> str:
        state = "configured" if self.is_configured(self.config) else "not configured"
        who = f"@{self.bot_username}" if self.bot_username else "(token not verified)"
        return f"telegram ({state}, {who})"


#: Kept for symmetry with other connectors that do need a third-party package.
REQUIRED_PACKAGES: Sequence[str] = ()


__all__ = ["API_ROOT", "HttpTelegramTransport", "TelegramConnector", "TelegramTransport"]
