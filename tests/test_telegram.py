"""Telegram connector tests, driven by a fake Bot API transport.

No network and no real token: ``FakeTransport`` records the Bot API calls and
replays canned responses, so the connector's own logic is what is under test.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from ackstreet.config import Config
from ackstreet.connectors.base import ConnectorError
from ackstreet.connectors.telegram import (
    HttpTelegramTransport,
    TelegramConnector,
    TelegramTransport,
)


class FakeTransport(TelegramTransport):
    """Records calls and returns scripted results."""

    def __init__(self, responses: Optional[Dict[str, Any]] = None) -> None:
        self.responses = dict(responses or {})
        self.calls: List[tuple] = []
        self.raises: Optional[Exception] = None

    def call(self, method: str, payload: Dict[str, Any]) -> Any:
        self.calls.append((method, payload))
        if self.raises is not None:
            raise self.raises
        if method in self.responses:
            value = self.responses[method]
            return value() if callable(value) else value
        if method == "getMe":
            return {"id": 1, "username": "ackstreet_test_bot", "is_bot": True}
        if method == "getUpdates":
            return []
        if method == "sendMessage":
            return {"message_id": len(self.calls)}
        return True

    def methods(self) -> List[str]:
        return [name for name, _payload in self.calls]

    def payload_for(self, method: str) -> Optional[Dict[str, Any]]:
        for name, payload in self.calls:
            if name == method:
                return payload
        return None


def update(
    text: str = "hello",
    chat_id: int = 100,
    user_id: int = 42,
    chat_type: str = "private",
    update_id: int = 1,
) -> Dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 5,
            "from": {"id": user_id, "first_name": "Ada", "last_name": "Lovelace"},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


@pytest.fixture()
def connector(config: Config) -> TelegramConnector:
    return TelegramConnector(config, transport=FakeTransport())


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

class TestCredentials:
    def test_not_configured_without_a_token(self, config: Config) -> None:
        assert TelegramConnector.is_configured(config) is False

    def test_store_and_reload(self, config: Config) -> None:
        TelegramConnector.store_credentials(config, token="123:abc")
        assert TelegramConnector.is_configured(config) is True

        reloaded = Config.load(config.path)
        assert TelegramConnector(reloaded).token == "123:abc"

    def test_env_var_wins_over_config(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        TelegramConnector.store_credentials(config, token="from-file")
        monkeypatch.setenv("ACKSTREET_TELEGRAM_BOT_TOKEN", "from-env")
        assert TelegramConnector(config).token == "from-env"

    def test_telegram_bot_token_env_alias(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "alias-env")
        assert TelegramConnector(config).token == "alias-env"
        assert TelegramConnector.is_configured(config) is True

    def test_get_me_records_the_username(self, connector: TelegramConnector) -> None:
        me = connector.get_me()
        assert me["username"] == "ackstreet_test_bot"
        assert "ackstreet_test_bot" in connector.describe()


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

class TestParsing:
    def test_private_message(self) -> None:
        incoming = TelegramConnector.parse_update(update("hi there", chat_id=5, user_id=9))
        assert incoming is not None
        assert incoming.platform == "telegram"
        assert incoming.chat_id == "5"
        assert incoming.user_id == "9"
        assert incoming.text == "hi there"
        assert incoming.chat_type == "private"
        assert incoming.session_key == "telegram:5"

    @pytest.mark.parametrize("chat_type", ["group", "supergroup"])
    def test_group_chat_is_marked(self, chat_type: str) -> None:
        incoming = TelegramConnector.parse_update(
            update(chat_id=-100, chat_type=chat_type)
        )
        assert incoming is not None
        assert incoming.chat_type == "group"

    def test_non_text_updates_are_ignored(self) -> None:
        assert TelegramConnector.parse_update({"update_id": 1}) is None
        assert TelegramConnector.parse_update({"update_id": 1, "message": {}}) is None
        assert (
            TelegramConnector.parse_update(
                {"update_id": 1, "message": {"chat": {"id": 1}, "text": ""}}
            )
            is None
        )
        assert (
            TelegramConnector.parse_update(
                {"update_id": 1, "message": {"chat": {"id": 1}, "photo": []}}
            )
            is None
        )

    def test_caption_counts_as_text(self) -> None:
        payload = update()
        payload["message"].pop("text")
        payload["message"]["caption"] = "a photo of a cat"
        incoming = TelegramConnector.parse_update(payload)
        assert incoming is not None
        assert incoming.text == "a photo of a cat"

    def test_display_name_falls_back_to_username(self) -> None:
        payload = update()
        payload["message"]["from"] = {"id": 3, "username": "ada"}
        incoming = TelegramConnector.parse_update(payload)
        assert incoming is not None
        assert incoming.user_name == "ada"


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------

class TestSending:
    def test_send_returns_the_message_id(self, config: Config) -> None:
        transport = FakeTransport({"sendMessage": {"message_id": 77}})
        connector = TelegramConnector(config, transport=transport)
        assert connector.send("100", "hello") == "77"
        payload = transport.payload_for("sendMessage")
        assert payload["chat_id"] == "100"
        assert payload["text"] == "hello"

    def test_reply_threads_the_original(self, config: Config) -> None:
        transport = FakeTransport()
        connector = TelegramConnector(config, transport=transport)
        connector.send("100", "hello", reply_to="5")
        assert transport.payload_for("sendMessage")["reply_to_message_id"] == "5"

    def test_long_message_is_split(self, config: Config) -> None:
        transport = FakeTransport()
        connector = TelegramConnector(config, transport=transport)
        connector.chunk_limit = 50
        connector.send_long("100", "y" * 130)
        sends = [p for m, p in transport.calls if m == "sendMessage"]
        assert len(sends) == 3
        assert all(len(p["text"]) <= 50 for p in sends)

    def test_send_typing_swallows_errors(self, config: Config) -> None:
        transport = FakeTransport()
        transport.raises = ConnectorError("flaky")
        connector = TelegramConnector(config, transport=transport)
        connector.send_typing("100")  # must not raise

    def test_edit(self, config: Config) -> None:
        transport = FakeTransport()
        connector = TelegramConnector(config, transport=transport)
        assert connector.edit("100", "5", "new text") is True
        assert "editMessageText" in transport.methods()


# --------------------------------------------------------------------------
# API error handling (HttpTelegramTransport)
# --------------------------------------------------------------------------

class TestHttpTransport:
    def _client(self, status: int = 200, body: Any = None, raise_exc: Any = None):
        class Response:
            def __init__(self) -> None:
                self.status_code = status
                self.text = "body"

            def json(self) -> Any:
                if body is None:
                    raise ValueError("no json")
                return body

        class Client:
            def __init__(self) -> None:
                self.requests: List[Any] = []

            def post(self, url: str, json: Any = None, timeout: Any = None) -> Any:
                self.requests.append((url, json, timeout))
                if raise_exc is not None:
                    raise raise_exc
                return Response()

            def close(self) -> None:
                pass

        return Client()

    def test_missing_token_is_a_clear_error(self, config: Config) -> None:
        transport = HttpTelegramTransport("", client=self._client())  # type: ignore[arg-type]
        with pytest.raises(ConnectorError, match="no Telegram bot token"):
            transport.call("getMe", {})

    def test_401_names_the_token_problem(self, config: Config) -> None:
        client = self._client(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
        transport = HttpTelegramTransport("bad", client=client)  # type: ignore[arg-type]
        with pytest.raises(ConnectorError, match="rejected the bot token"):
            transport.call("getMe", {})

    def test_409_explains_the_other_poller(self) -> None:
        client = self._client(
            409,
            {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates"},
        )
        transport = HttpTelegramTransport("tok", client=client)  # type: ignore[arg-type]
        with pytest.raises(ConnectorError, match="already polling"):
            transport.call("getUpdates", {})

    def test_non_json_body_is_reported(self) -> None:
        client = self._client(200, None)
        transport = HttpTelegramTransport("tok", client=client)  # type: ignore[arg-type]
        with pytest.raises(ConnectorError, match="non-JSON"):
            transport.call("getMe", {})

    def test_timeout_is_a_connector_error(self) -> None:
        import httpx

        client = self._client(raise_exc=httpx.TimeoutException("too slow"))
        transport = HttpTelegramTransport("tok", client=client)  # type: ignore[arg-type]
        with pytest.raises(ConnectorError, match="timed out"):
            transport.call("getMe", {})

    def test_http_error_is_a_connector_error(self) -> None:
        import httpx

        client = self._client(raise_exc=httpx.ConnectError("refused"))
        transport = HttpTelegramTransport("tok", client=client)  # type: ignore[arg-type]
        with pytest.raises(ConnectorError, match="request failed"):
            transport.call("getMe", {})

    def test_transport_error_names_the_url_and_uses_the_token(self) -> None:
        client = self._client(200, {"ok": True, "result": {"id": 1}})
        transport = HttpTelegramTransport("secret", client=client)  # type: ignore[arg-type]
        transport.call("getMe", {})
        url, _payload, _timeout = client.requests[0]
        assert url.endswith("/botsecret/getMe")

    def test_get_updates_gets_a_long_read_timeout(self) -> None:
        client = self._client(200, {"ok": True, "result": []})
        transport = HttpTelegramTransport("tok", timeout=5.0, client=client)  # type: ignore[arg-type]
        transport.call("getUpdates", {"timeout": 25})
        assert client.requests[0][2] == 40.0


# --------------------------------------------------------------------------
# listen loop
# --------------------------------------------------------------------------

class TestListen:
    def test_updates_are_dispatched_and_the_offset_advances(self, config: Config) -> None:
        batches = [[update("first", update_id=1), update("second", update_id=2)], []]

        transport = FakeTransport()
        transport.responses["getUpdates"] = lambda: batches.pop(0) if batches else []
        connector = TelegramConnector(config, transport=transport)

        seen: List[str] = []

        def on_message(incoming) -> None:
            seen.append(incoming.text)
            connector.stop()  # one pass is enough

        connector.listen(on_message=on_message)
        assert seen == ["first", "second"]
        # The next poll resumes after the highest update id seen.
        assert connector.offset == 3

    def test_transport_errors_do_not_end_the_loop(self, config: Config) -> None:
        transport = FakeTransport()
        state = {"calls": 0}
        connector = TelegramConnector(config, transport=transport)

        def flaky() -> List[Dict[str, Any]]:
            state["calls"] += 1
            if state["calls"] == 1:
                raise ConnectorError("temporary")
            connector.stop()
            return []

        transport.responses["getUpdates"] = flaky
        connector.listen(on_message=lambda m: None)

        assert state["calls"] >= 2, "the loop must retry after a transport failure"

    def test_handler_exception_does_not_stop_the_bot(self, config: Config) -> None:
        batches = [[update("boom", update_id=1), update("after", update_id=2)]]
        transport = FakeTransport()
        transport.responses["getUpdates"] = lambda: batches.pop(0) if batches else []
        connector = TelegramConnector(config, transport=transport)

        seen: List[str] = []

        def on_message(incoming) -> None:
            seen.append(incoming.text)
            if incoming.text == "boom":
                raise RuntimeError("handler blew up")
            connector.stop()

        connector.listen(on_message=on_message)
        assert seen == ["boom", "after"]

    def test_stop_is_honoured(self, config: Config) -> None:
        transport = FakeTransport()
        connector = TelegramConnector(config, transport=transport)
        connector.stop()
        connector.listen(on_message=lambda m: None)
        assert connector.stopping is True
