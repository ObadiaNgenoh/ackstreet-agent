"""WhatsApp connector tests, driven by a fake neonize-style client.

The neonize package is not installable in this environment, so the connector is
tested through its injectable client and event classes. That covers everything
except the real protocol handshake -- see the README status section.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ackstreet.config import Config
from ackstreet.connectors.base import ConnectorError, NotInstalledError
from ackstreet.connectors.whatsapp import WhatsAppConnector, render_qr_terminal


class FakeEvent:
    """Stands in for a neonize event class."""

    def __init__(self, label: str) -> None:
        self.label = label


class FakeClient:
    """Records sends and lets tests fire events, like a neonize client."""

    def __init__(self, supports_decorator: bool = True) -> None:
        self.sent: List[tuple] = []
        self.handlers: Dict[Any, Any] = {}
        self.connected = False
        self.disconnected = False
        self._supports_decorator = supports_decorator
        self._next_id = 0

    # -- neonize surface ---------------------------------------------------

    def event(self, event_type: Any):
        def decorator(func):
            self.handlers[event_type] = func
            return func

        return decorator

    def add_event_handler(self, event_type: Any, func: Any) -> None:
        self.handlers[event_type] = func

    def send_message(self, recipient: str, text: str) -> Any:
        self._next_id += 1
        self.sent.append((recipient, text))

        class Sent:
            ID = f"wa-{self._next_id}"

        return Sent()

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True

    # -- test helpers ------------------------------------------------------

    def fire(self, event_type: Any, payload: Any) -> None:
        handler = self.handlers.get(event_type)
        if handler is not None:
            handler(self, payload)


EVENT_TYPES = {
    "qr": FakeEvent("qr"),
    "message": FakeEvent("message"),
    "connected": FakeEvent("connected"),
}


class FakeMessage:
    """A protobuf-shaped inbound message."""

    def __init__(
        self,
        text: str = "hello",
        chat: str = "254700000001@s.whatsapp.net",
        sender: str = "254700000001@s.whatsapp.net",
        is_group: bool = False,
        push_name: str = "Ada",
    ) -> None:
        class Message:
            conversation = text

        self.Info = {
            "Chat": chat,
            "Sender": sender,
            "SenderAlt": sender,
            "IsGroup": is_group,
            "ID": "3EB0ABCDEF",
            "PushName": push_name,
        }
        self.Message = Message()


@pytest.fixture()
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture()
def connector(config: Config, fake_client: FakeClient) -> WhatsAppConnector:
    return WhatsAppConnector(
        config,
        client=fake_client,
        qr_renderer=lambda payload: f"[QR:{payload}]",
        event_types=EVENT_TYPES,
    )


# --------------------------------------------------------------------------
# session persistence
# --------------------------------------------------------------------------

class TestSession:
    def test_session_path_defaults_inside_the_home(self, config: Config) -> None:
        assert WhatsAppConnector(config).session_path == config.root / "whatsapp" / "session.db"

    def test_custom_session_path_is_persisted_and_reloaded(self, config: Config) -> None:
        target = config.root / "custom" / "wa.db"
        WhatsAppConnector.store_credentials(config, session_path=str(target))

        reloaded = Config.load(config.path)
        assert WhatsAppConnector(reloaded).session_path == target

    def test_env_var_overrides_the_path(
        self, config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        override = tmp_path / "env.db"
        monkeypatch.setenv("ACKSTREET_WHATSAPP_SESSION", str(override))
        assert WhatsAppConnector(config).session_path == override

    def test_not_configured_until_the_session_file_exists(self, config: Config) -> None:
        assert WhatsAppConnector.is_configured(config) is False
        path = WhatsAppConnector(config).session_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"session")
        assert WhatsAppConnector.is_configured(config) is True
        assert "session found" in WhatsAppConnector(config).describe()


# --------------------------------------------------------------------------
# optional dependency
# --------------------------------------------------------------------------

class TestDependency:
    def test_missing_client_points_at_the_extra(self, config: Config) -> None:
        connector = WhatsAppConnector(config)  # no injected client
        with pytest.raises(NotInstalledError) as caught:
            _ = connector.client
        message = str(caught.value)
        assert "neonize" in message
        assert "ackstreet-agent[whatsapp]" in message

    def test_missing_event_types_point_at_the_extra(self, config: Config) -> None:
        connector = WhatsAppConnector(config, client=FakeClient())
        with pytest.raises(NotInstalledError):
            connector._resolve_event_types()

    def test_qr_renderer_explains_itself_without_qrcode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def blocked(name: str, *args: Any, **kwargs: Any):
            if name == "qrcode":
                raise ImportError("no qrcode")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        rendered = render_qr_terminal("some-payload")
        assert "some-payload" in rendered
        assert "ackstreet-agent[whatsapp]" in rendered

    def test_empty_qr_payload(self) -> None:
        assert render_qr_terminal("") == "(no QR payload received)"


# --------------------------------------------------------------------------
# QR flow
# --------------------------------------------------------------------------

class TestQR:
    def test_qr_payload_is_rendered_and_kept(self, connector: WhatsAppConnector) -> None:
        rendered = connector.handle_qr("2@abc,def==")
        assert rendered == "[QR:2@abc,def==]"
        assert connector.last_qr == "2@abc,def=="

    def test_setup_instructions_mention_the_app(
        self, connector: WhatsAppConnector, capsys
    ) -> None:
        connector.print_setup_instructions()
        output = capsys.readouterr().out
        assert "Linked devices" in output
        assert str(connector.session_path) in output


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

class TestParsing:
    def test_protobuf_style_message(self, connector: WhatsAppConnector) -> None:
        incoming = connector.parse_event(FakeMessage("hi there"))
        assert incoming is not None
        assert incoming.platform == "whatsapp"
        assert incoming.text == "hi there"
        assert incoming.chat_id == "254700000001@s.whatsapp.net"
        assert incoming.chat_type == "private"
        assert incoming.user_name == "Ada"

    def test_dict_style_message(self, connector: WhatsAppConnector) -> None:
        payload = {
            "Info": {"Chat": "123@s.whatsapp.net", "Sender": "123@s.whatsapp.net"},
            "Message": {"conversation": "from a dict"},
        }
        incoming = connector.parse_event(payload)
        assert incoming is not None
        assert incoming.text == "from a dict"

    def test_extended_text_message(self, connector: WhatsAppConnector) -> None:
        payload = {
            "Info": {"Chat": "123@s.whatsapp.net", "Sender": "123@s.whatsapp.net"},
            "Message": {"extendedTextMessage": {"text": "quoted reply"}},
        }
        incoming = connector.parse_event(payload)
        assert incoming is not None
        assert incoming.text == "quoted reply"

    def test_group_message_is_flagged(self, connector: WhatsAppConnector) -> None:
        incoming = connector.parse_event(
            FakeMessage("hey all", chat="123@g.us", is_group=True)
        )
        assert incoming is not None
        assert incoming.chat_type == "group"

    def test_group_chat_detected_from_jid_alone(self, connector: WhatsAppConnector) -> None:
        incoming = connector.parse_event(
            FakeMessage("hey", chat="123@g.us", is_group=False)
        )
        assert incoming is not None
        assert incoming.chat_type == "group"

    def test_media_only_message_is_ignored(self, connector: WhatsAppConnector) -> None:
        payload = {
            "Info": {"Chat": "1@s.whatsapp.net", "Sender": "1@s.whatsapp.net"},
            "Message": {"imageMessage": {"caption": None}},
        }
        assert connector.parse_event(payload) is None

    def test_missing_chat_is_ignored(self, connector: WhatsAppConnector) -> None:
        payload = {"Info": {}, "Message": {"conversation": "hi"}}
        assert connector.parse_event(payload) is None


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------

class TestSending:
    def test_send_normalises_a_bare_number(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        assert connector.send("254700000001", "hello") == "wa-1"
        assert fake_client.sent[0][0] == "254700000001@s.whatsapp.net"

    def test_send_keeps_an_existing_jid(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        connector.send("123@g.us", "hi group")
        assert fake_client.sent[0][0] == "123@g.us"

    def test_long_message_is_split(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        connector.chunk_limit = 30
        connector.send_long("254700000001", "z" * 90)
        assert len(fake_client.sent) == 3
        assert all(len(text) <= 30 for _chat, text in fake_client.sent)

    def test_send_failure_becomes_a_connector_error(self, config: Config) -> None:
        class Broken(FakeClient):
            def send_message(self, recipient: str, text: str) -> Any:
                raise RuntimeError("socket closed")

        connector = WhatsAppConnector(config, client=Broken(), event_types=EVENT_TYPES)
        with pytest.raises(ConnectorError, match="socket closed"):
            connector.send("254700000001", "hi")


# --------------------------------------------------------------------------
# event wiring
# --------------------------------------------------------------------------

class TestEventWiring:
    def test_handlers_are_registered_via_the_decorator(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        handlers = connector.wire_events(fake_client, lambda m: None)
        assert set(handlers) == {"qr", "connected", "message"}
        assert EVENT_TYPES["message"] in fake_client.handlers
        assert EVENT_TYPES["qr"] in fake_client.handlers

    def test_add_event_handler_is_also_supported(self, config: Config) -> None:
        client = FakeClient(supports_decorator=False)
        connector = WhatsAppConnector(config, client=client, event_types=EVENT_TYPES)
        connector.wire_events(client, lambda m: None)
        assert client.handlers

    def test_client_without_event_api_is_rejected(self, config: Config) -> None:
        class NotAClient:
            pass

        connector = WhatsAppConnector(config, client=NotAClient(), event_types=EVENT_TYPES)
        with pytest.raises(ConnectorError, match="not a neonize client"):
            connector.wire_events(NotAClient(), lambda m: None)

    def test_incoming_message_reaches_the_handler(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        seen: List[Any] = []
        connector.wire_events(fake_client, seen.append)
        fake_client.fire(EVENT_TYPES["message"], FakeMessage("routed"))
        assert len(seen) == 1
        assert seen[0].text == "routed"

    def test_handler_exception_does_not_escape(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        def boom(_message: Any) -> None:
            raise RuntimeError("handler failed")

        connector.wire_events(fake_client, boom)
        fake_client.fire(EVENT_TYPES["message"], FakeMessage("x"))
        assert any("handler failed" in line for line in connector._log)

    def test_connected_event_is_recorded(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        connector.wire_events(fake_client, lambda m: None)
        fake_client.fire(EVENT_TYPES["connected"], None)
        assert connector.connected.is_set()

    def test_media_event_is_dropped_silently(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        seen: List[Any] = []
        connector.wire_events(fake_client, seen.append)
        fake_client.fire(
            EVENT_TYPES["message"],
            {"Info": {"Chat": "1@s.whatsapp.net"}, "Message": {"imageMessage": {}}},
        )
        assert seen == []


# --------------------------------------------------------------------------
# listen
# --------------------------------------------------------------------------

def _set_event():
    event = threading.Event()
    event.set()
    return event


class TestListen:
    def test_listen_connects_and_wires_events(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        connector.listen(on_message=lambda m: None, stop_event=_set_event())
        assert fake_client.connected is True
        assert EVENT_TYPES["message"] in fake_client.handlers

    def test_listen_without_a_handler_is_an_error(self, connector: WhatsAppConnector) -> None:
        with pytest.raises(ConnectorError, match="no message handler"):
            connector.listen()

    def test_close_disconnects(
        self, connector: WhatsAppConnector, fake_client: FakeClient
    ) -> None:
        connector.close()
        assert fake_client.disconnected is True
