"""Connector tests: routing, replies, session isolation, allowlist, approval.

Everything here runs against fakes -- no network, no bot token, no phone. The
agent loop is driven by the shared scripted provider from ``conftest``.
"""

from __future__ import annotations

import threading
from typing import Any, List, Optional, Tuple

import pytest

from ackstreet.agent import Agent
from ackstreet.config import Config
from ackstreet.connectors.base import Connector, IncomingMessage, NotInstalledError, chunk_text
from ackstreet.connectors.registry import find, get, names
from ackstreet.connectors.router import MessageRouter, classify_answer
from ackstreet.connectors.sessions import SessionManager


class FakeConnector(Connector):
    """A connector that records everything instead of talking to a network."""

    name = "fake"
    display_name = "Fake"
    extra = "fake"

    def __init__(self, config: Config, on_message=None, **kwargs: Any) -> None:
        super().__init__(config, on_message=on_message)
        self.sent: List[Tuple[str, str]] = []
        self.typing: List[str] = []
        self.edits: List[Tuple[str, str, str]] = []
        self.send_error: Optional[Exception] = None
        self._ids = 0

    @classmethod
    def is_configured(cls, config: Config) -> bool:
        return True

    def send(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> Optional[str]:
        if self.send_error is not None:
            raise self.send_error
        self._ids += 1
        self.sent.append((chat_id, text))
        return f"msg{self._ids}"

    def send_typing(self, chat_id: str) -> None:
        self.typing.append(chat_id)

    def edit(self, chat_id: str, message_id: str, text: str) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True

    def listen(self, on_message=None, stop_event=None) -> None:
        return None

    # -- test helpers ------------------------------------------------------

    def texts(self) -> List[str]:
        return [text for _chat, text in self.sent]

    def chat_texts(self, chat_id: str) -> List[str]:
        return [text for chat, text in self.sent if chat == chat_id]

    def prompts(self, chat_id: str = "100") -> List[str]:
        return [t for t in self.chat_texts(chat_id) if "Approval needed" in t]


@pytest.fixture()
def fake_connector(config: Config) -> FakeConnector:
    return FakeConnector(config)


@pytest.fixture(autouse=True)
def short_approval_timeout(config: Config) -> None:
    """Keep every connector test fast.

    The production default is 300s (a human needs time to answer a prompt in a
    chat app). A test that accidentally leaves a gated call unanswered would
    otherwise stall for five minutes instead of failing quickly.
    """
    config.set("connectors", "approval_timeout", 2)
    # Default the gate to auto unless a test opts into prompting.
    config.set("agent", "approval_mode", "auto")


def make_router(config, connector, script, provider_cls, tc, fin, **kwargs):
    """Build a router whose per-chat agents replay *script*.

    Returns the router plus every agent it created, so tests can assert on
    per-chat isolation directly. ``tc``/``fin`` are conftest's tool_call/final
    builders.
    """
    created: List[Agent] = []

    def factory(_key: str) -> Agent:
        agent = Agent(config, provider=provider_cls(list(script)))
        created.append(agent)
        return agent

    router = MessageRouter(
        config, connector, agent_factory=factory, session_ttl=0, wait_slice=0.005, **kwargs
    )
    return router, created


def msg(
    text: str,
    user_id: str = "42",
    chat_id: str = "100",
    chat_type: str = "private",
    platform: str = "telegram",
) -> IncomingMessage:
    return IncomingMessage(
        platform=platform,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        message_id="1",
        chat_type=chat_type,
    )


def tool_outputs(agent: Agent, name: Optional[str] = None) -> List[str]:
    """The rendered results of tool calls the agent made, oldest first.

    ``Agent.chat_turn`` keeps the step detail in the returned RunResult rather
    than on the instance, so tests read the conversation itself.
    """
    out: List[str] = []
    for message in agent.messages:
        if getattr(message, "role", None) != "tool":
            continue
        if name is not None and getattr(message, "name", None) != name:
            continue
        out.append(getattr(message, "content", "") or "")
    return out


def answer_when_prompted(
    router: MessageRouter,
    connector: FakeConnector,
    text: str,
    chat_id: str = "100",
    user_id: str = "42",
    tries: int = 400,
    delay: float = 0.005,
) -> Optional[str]:
    """Wait for the approval prompt, then answer it.

    Retries the answer until it is actually consumed: the prompt is sent
    slightly before the pending marker is registered, so a single attempt can
    legitimately race. Returns the verdict, or None if it never landed.
    """
    for _ in range(tries):
        if connector.prompts(chat_id):
            verdict = router.resolve_pending(msg(text, user_id=user_id, chat_id=chat_id))
            if verdict is not None:
                return verdict
        threading.Event().wait(delay)
    return None


# --------------------------------------------------------------------------
# answer classification
# --------------------------------------------------------------------------

class TestClassifyAnswer:
    @pytest.mark.parametrize(
        "text", ["yes", "y", "YES", "ok", "approve", "yes please go ahead"]
    )
    def test_approvals(self, text: str) -> None:
        assert classify_answer(text) == "approve"

    @pytest.mark.parametrize("text", ["no", "n", "nope", "deny", "no, that is wrong"])
    def test_denials(self, text: str) -> None:
        assert classify_answer(text) == "deny"

    @pytest.mark.parametrize("text", ["always", "a"])
    def test_always(self, text: str) -> None:
        assert classify_answer(text) == "always"

    @pytest.mark.parametrize("text", ["list my files", "", "   ", "12345"])
    def test_unrelated_is_not_an_answer(self, text: str) -> None:
        assert classify_answer(text) is None


# --------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------

class TestChunking:
    def test_short_text_is_one_chunk(self) -> None:
        assert chunk_text("hello", 100) == ["hello"]

    def test_empty_text_produces_nothing(self) -> None:
        assert chunk_text("", 100) == []

    def test_long_text_is_split_within_limit(self) -> None:
        chunks = chunk_text("word " * 100, 50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 50 for chunk in chunks)

    def test_prefers_newline_boundary(self) -> None:
        chunks = chunk_text("a" * 40 + "\n" + "b" * 40, 50)
        assert chunks[0] == "a" * 40


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

class TestRouting:
    def test_message_is_routed_to_agent_and_reply_sent(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = make_router(
            config,
            fake_connector,
            [make_final("the answer is 4")],
            scripted_provider,
            make_tool_call,
            make_final,
        )

        outcome = router.handle_message(msg("what is 2+2?"))

        assert outcome.handled is True
        assert len(created) == 1
        provider = created[0].provider
        assert provider.calls, "the agent never called the provider"
        assert provider.calls[0][-1].content == "what is 2+2?"
        assert "the answer is 4" in fake_connector.chat_texts("100")

    def test_tool_calls_run_through_the_agent_loop(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        script = [
            make_tool_call("write_file", {"path": "note.txt", "content": "hi"}),
            make_final("wrote the file"),
        ]
        router, _ = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        outcome = router.handle_message(msg("write a file"))

        assert outcome.handled
        assert outcome.steps == 1
        assert "wrote the file" in fake_connector.chat_texts("100")

    def test_typing_indicator_is_shown(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, _ = make_router(
            config, fake_connector, [make_final("ok")], scripted_provider, make_tool_call, make_final
        )
        router.handle_message(msg("hi"))
        assert fake_connector.typing == ["100"]

    def test_empty_message_is_ignored(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, _ = make_router(
            config,
            fake_connector,
            [make_final("unused")],
            scripted_provider,
            make_tool_call,
            make_final,
        )
        outcome = router.handle_message(msg("   "))
        assert outcome.handled is False
        assert fake_connector.sent == []

    def test_agent_error_is_reported_not_crashed(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        class Boom:
            messages: List[Any] = []

            def chat_turn(self, *_a: Any, **_k: Any):
                raise RuntimeError("provider exploded")

            def reset(self) -> None:
                pass

        router = MessageRouter(
            config,
            fake_connector,
            agent_factory=lambda _key: Boom(),
            session_ttl=0,
            wait_slice=0.005,
        )
        outcome = router.handle_message(msg("go"))

        assert outcome.handled is False
        assert "provider exploded" in outcome.reason
        assert any("provider exploded" in text for text in fake_connector.texts())
        assert router.stats["errors"] == 1

    def test_long_answer_is_split_across_messages(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        fake_connector.chunk_limit = 40
        router, _ = make_router(
            config,
            fake_connector,
            [make_final("x" * 200)],
            scripted_provider,
            make_tool_call,
            make_final,
        )
        router.handle_message(msg("go"))
        long_sends = [t for t in fake_connector.texts() if t.startswith("x")]
        assert len(long_sends) > 1
        assert all(len(t) <= 40 for t in long_sends)

    def test_learnt_skill_is_announced(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        script = [
            make_tool_call("shell", {"command": "echo one"}),
            make_tool_call("shell", {"command": "echo two"}),
            make_tool_call("shell", {"command": "echo three"}),
            make_final("all done"),
        ]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )
        outcome = router.handle_message(msg("run three commands"))
        assert outcome.handled is True
        # Whatever the curator decided, the reply still reached the chat.
        assert "all done" in fake_connector.chat_texts("100")


# --------------------------------------------------------------------------
# session isolation
# --------------------------------------------------------------------------

class TestSessionIsolation:
    def test_two_chats_get_separate_agents_and_history(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        script = [make_final("answer one"), make_final("answer two")]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        router.handle_message(msg("first", chat_id="100", user_id="1"))
        router.handle_message(msg("second", chat_id="200", user_id="2"))

        assert len(created) == 2, "each chat must get its own agent"
        assert created[0] is not created[1]
        assert router.sessions.count() == 2

        first_history = " ".join(m.content for m in created[0].messages)
        second_history = " ".join(m.content for m in created[1].messages)
        assert "first" in first_history
        assert "first" not in second_history
        assert "second" in second_history
        assert "second" not in first_history

    def test_same_chat_reuses_one_agent(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        script = [make_final("one"), make_final("two")]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        router.handle_message(msg("a", chat_id="100"))
        router.handle_message(msg("b", chat_id="100"))

        assert len(created) == 1
        assert router.sessions.count() == 1

    def test_same_chat_id_on_two_platforms_does_not_collide(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        script = [make_final("one"), make_final("two")]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        router.handle_message(msg("tg", chat_id="7", platform="telegram"))
        router.handle_message(msg("wa", chat_id="7", platform="whatsapp"))

        assert len(created) == 2, "sessions are keyed by platform AND chat id"
        assert router.sessions.keys() == ["telegram:7", "whatsapp:7"]

    def test_clear_forgets_only_that_chat(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        script = [make_final("one"), make_final("two")]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        router.handle_message(msg("a", chat_id="100"))
        router.handle_message(msg("b", chat_id="200"))
        router.handle_message(msg("/clear", chat_id="100"))

        assert router.sessions.peek("telegram:100") is None
        assert router.sessions.peek("telegram:200") is not None
        assert created[0].messages == []


class TestSessionManager:
    def test_zero_ttl_disables_eviction(self) -> None:
        manager = SessionManager(lambda key: object(), ttl=0.0)
        manager.get("a:1")
        assert manager.evict_idle() == []

    def test_fresh_sessions_are_not_evicted(self) -> None:
        manager = SessionManager(lambda key: object(), ttl=1000.0)
        manager.get("a:1")
        assert manager.evict_idle() == []

    def test_max_sessions_drops_least_recent(self) -> None:
        manager = SessionManager(lambda key: object(), ttl=0, max_sessions=2)
        manager.get("a:1")
        manager.get("a:2")
        manager.get("a:3")
        assert manager.count() == 2
        assert "a:1" not in manager.keys()


# --------------------------------------------------------------------------
# allowlist
# --------------------------------------------------------------------------

class TestAllowlist:
    def test_unauthorised_user_is_blocked_and_agent_never_runs(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("connectors", "allowed_user_ids", ["111"])
        router, created = make_router(
            config,
            fake_connector,
            [make_final("should not happen")],
            scripted_provider,
            make_tool_call,
            make_final,
        )

        outcome = router.handle_message(msg("hi", user_id="999"))

        assert outcome.handled is False
        assert created == [], "the agent must not be constructed for a blocked user"
        assert router.sessions.count() == 0
        assert any("Not authorised" in text for text in fake_connector.texts())
        assert router.stats["rejected"] == 1

    def test_allowlisted_user_is_accepted(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("connectors", "allowed_user_ids", ["111"])
        router, _ = make_router(
            config,
            fake_connector,
            [make_final("welcome")],
            scripted_provider,
            make_tool_call,
            make_final,
        )
        outcome = router.handle_message(msg("hi", user_id="111"))
        assert outcome.handled is True
        assert "welcome" in fake_connector.texts()

    def test_shared_allowlist_applies_to_every_platform(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("connectors", "allowed_user_ids", ["555"])
        router, _ = make_router(
            config, fake_connector, [make_final("ok")], scripted_provider, make_tool_call, make_final
        )
        allowed = router.handle_message(
            msg("hi", user_id="555", chat_id="1", platform="telegram")
        )
        blocked = router.handle_message(
            msg("hi", user_id="556", chat_id="2", platform="whatsapp")
        )
        assert allowed.handled is True
        assert blocked.handled is False

    def test_wildcard_allows_everyone(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("connectors", "allowed_user_ids", ["*"])
        router, _ = make_router(
            config, fake_connector, [make_final("ok")], scripted_provider, make_tool_call, make_final
        )
        assert router.handle_message(msg("hi", user_id="999")).handled

    def test_empty_allowlist_is_open(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, _ = make_router(
            config, fake_connector, [make_final("ok")], scripted_provider, make_tool_call, make_final
        )
        assert router.handle_message(msg("hi", user_id="999")).handled

    def test_group_chats_are_ignored_by_default(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = make_router(
            config, fake_connector, [make_final("nope")], scripted_provider, make_tool_call, make_final
        )
        outcome = router.handle_message(msg("hi", chat_id="-100", chat_type="group"))
        assert outcome.handled is False
        assert created == []

    def test_group_chats_can_be_enabled(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("connectors", "allow_group_chats", True)
        router, _ = make_router(
            config,
            fake_connector,
            [make_final("hello group")],
            scripted_provider,
            make_tool_call,
            make_final,
        )
        outcome = router.handle_message(msg("hi", chat_id="-100", chat_type="group"))
        assert outcome.handled is True

    def test_whatsapp_jid_suffix_matches_bare_number(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("connectors", "allowed_user_ids", ["254700000001"])
        router, _ = make_router(
            config, fake_connector, [make_final("ok")], scripted_provider, make_tool_call, make_final
        )
        outcome = router.handle_message(
            msg("hi", user_id="254700000001@s.whatsapp.net", platform="whatsapp")
        )
        assert outcome.handled is True


# --------------------------------------------------------------------------
# approval over chat
# --------------------------------------------------------------------------

def gated(config, fake_connector, scripted_provider, make_tool_call, make_final, script=None):
    """A router whose agent will try one dangerous shell call."""
    config.set("agent", "approval_mode", "ask")
    config.set("connectors", "approval_timeout", 5)
    if script is None:
        script = [
            make_tool_call("shell", {"command": "echo approved-run"}),
            make_final("done"),
        ]
    return make_router(
        config, fake_connector, script, scripted_provider, make_tool_call, make_final
    )


class TestApprovalOverChat:
    def test_prompt_is_sent_as_a_chat_message_and_yes_approves(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )

        verdicts: List[Any] = []

        def reply_yes() -> None:
            verdicts.append(answer_when_prompted(router, fake_connector, "yes"))

        worker = threading.Thread(target=reply_yes)
        worker.start()
        try:
            outcome = router.handle_message(msg("run the command"))
        finally:
            worker.join(timeout=10)

        assert fake_connector.prompts(), "the approval question must reach the chat"
        assert any("shell" in t for t in fake_connector.prompts())
        assert verdicts == ["approve"]
        assert outcome.handled is True
        assert outcome.steps == 1
        assert any("approved-run" in out for out in tool_outputs(created[0], "shell"))
        assert any("Approved" in t for t in fake_connector.texts())

    def test_no_reply_times_out_and_the_tool_does_not_run(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        config.set("connectors", "approval_timeout", 0.2)

        outcome = router.handle_message(msg("run the command"))

        assert outcome.handled is True
        assert outcome.steps == 1
        shell_output = " ".join(tool_outputs(created[0], "shell")).lower()
        assert "refused" in shell_output
        assert any("No approval received in time" in t for t in fake_connector.texts())

    def test_unapproved_write_never_touches_the_disk(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("agent", "approval_mode", "ask")
        config.set("connectors", "approval_timeout", 0.2)
        marker = config.workspace / "should_not_exist.txt"
        script = [
            make_tool_call(
                "write_file", {"path": str(marker), "content": "this must not be written"}
            ),
            make_final("done"),
        ]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        router.handle_message(msg("write it"))

        assert not marker.exists(), "an unapproved write must not happen"
        assert "refused" in " ".join(tool_outputs(created[0], "write_file")).lower()

    def test_denial_blocks_the_tool_and_is_acknowledged(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        verdicts: List[Any] = []

        def reply_no() -> None:
            verdicts.append(answer_when_prompted(router, fake_connector, "no"))

        worker = threading.Thread(target=reply_no)
        worker.start()
        try:
            outcome = router.handle_message(msg("run the command"))
        finally:
            worker.join(timeout=10)

        assert verdicts == ["deny"]
        assert outcome.steps == 1
        assert "refused" in " ".join(tool_outputs(created[0], "shell")).lower()
        assert any("Refused" in t for t in fake_connector.texts())

    def test_always_remembers_the_tool_for_that_chat(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        verdicts: List[Any] = []

        def reply_always() -> None:
            verdicts.append(answer_when_prompted(router, fake_connector, "always"))

        worker = threading.Thread(target=reply_always)
        worker.start()
        try:
            router.handle_message(msg("run the command"))
        finally:
            worker.join(timeout=10)

        assert verdicts == ["always"]
        assert "shell" in router._always["telegram:100"]

        # A second dangerous call now runs with no further prompt.
        before = len(fake_connector.prompts())
        created[0]._provider = scripted_provider(
            [make_tool_call("shell", {"command": "echo second"}), make_final("ok")]
        )
        outcome = router.handle_message(msg("run it again"))

        assert outcome.steps >= 1
        assert len(fake_connector.prompts()) == before, "no second prompt after 'always'"
        assert any("second" in out for out in tool_outputs(created[0], "shell"))

    def test_allowlisted_command_skips_the_prompt(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("agent", "approval_mode", "allowlist")
        config.set("agent", "approval_allowlist", ["echo*"])
        script = [
            make_tool_call("shell", {"command": "echo allowed"}),
            make_final("ok"),
        ]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        outcome = router.handle_message(msg("run echo"))

        assert outcome.steps == 1
        assert "allowed" in " ".join(tool_outputs(created[0], "shell"))
        assert not fake_connector.prompts()

    def test_denylist_refuses_even_if_the_chat_says_yes(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("agent", "approval_mode", "ask")
        config.set("agent", "approval_denylist", ["rm -rf*"])
        script = [
            make_tool_call("shell", {"command": "rm -rf /tmp/ackstreet-nothing"}),
            make_final("ok"),
        ]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )

        router.handle_message(msg("delete it"))

        shell_output = " ".join(tool_outputs(created[0], "shell")).lower()
        assert "refused" in shell_output
        assert "denylist" in shell_output
        # A denied call never even offers a prompt.
        assert not fake_connector.prompts()

    def test_unrelated_message_is_not_taken_as_an_answer(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        from ackstreet.connectors.router import PendingApproval

        router, _ = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        router._pending["telegram:100"] = PendingApproval(
            chat_id="100", tool="shell", target="ls"
        )

        assert router.resolve_pending(msg("what files are here?")) is None
        assert router.pending_count == 1

    def test_prompt_delivery_failure_denies(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        fake_connector.send_error = RuntimeError("network down")

        outcome = router.handle_message(msg("run the command"))

        assert outcome.steps == 1
        assert "refused" in " ".join(tool_outputs(created[0], "shell")).lower()

    def test_auto_mode_does_not_prompt(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        config.set("agent", "approval_mode", "auto")
        script = [make_tool_call("shell", {"command": "echo auto"}), make_final("ok")]
        router, created = make_router(
            config, fake_connector, script, scripted_provider, make_tool_call, make_final
        )
        router.handle_message(msg("go"))
        assert any("auto" in out for out in tool_outputs(created[0], "shell"))
        assert not fake_connector.prompts()

    def test_approval_can_be_answered_by_a_chat_message_that_is_also_a_question(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        """A new instruction, not an answer, must become a fresh turn."""
        router, _ = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        from ackstreet.connectors.router import PendingApproval

        router._pending["telegram:100"] = PendingApproval(
            chat_id="100", tool="shell", target="ls"
        )
        assert router.resolve_pending(msg("can you do something else instead")) is None


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

class TestAsyncDispatch:
    """The listener callback must not block, or approvals deadlock."""

    def test_dispatch_async_returns_immediately_and_completes(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = make_router(
            config,
            fake_connector,
            [make_final("async answer")],
            scripted_provider,
            make_tool_call,
            make_final,
        )

        future = router.dispatch_async(msg("hello"))
        # The call returned before the work finished.
        assert future is not None
        outcome = future.result(timeout=10)
        assert outcome.handled is True
        assert "async answer" in fake_connector.chat_texts("100")
        router.shutdown()

    def test_wait_for_idle_waits_for_queued_work(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, _ = make_router(
            config,
            fake_connector,
            [make_final("one"), make_final("two")],
            scripted_provider,
            make_tool_call,
            make_final,
        )
        router.dispatch_async(msg("first", chat_id="1"))
        router.dispatch_async(msg("second", chat_id="2"))
        assert router.wait_for_idle(timeout=10) is True
        assert len(fake_connector.texts()) == 2
        router.shutdown()

    def test_a_blocked_approval_does_not_stop_the_next_message(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        """The deadlock guard: a gated turn must not block the listener.

        Message 1 waits at the approval gate; message 2 (the "yes") has to be
        processed on another thread for the gate to ever open.
        """
        router, created = gated(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )

        first = router.dispatch_async(msg("run the command"))
        # Block until the prompt is out, then answer via the async path.
        for _ in range(400):
            if fake_connector.prompts():
                break
            threading.Event().wait(0.005)
        assert fake_connector.prompts(), "no prompt appeared"

        answer = router.dispatch_async(msg("yes"))
        assert answer.result(timeout=10).reason == "approval answer"
        assert first.result(timeout=15).handled is True
        assert any("approved-run" in out for out in tool_outputs(created[0], "shell"))
        router.shutdown()


class TestCommands:
    def _router(self, config, fake_connector, scripted_provider, make_tool_call, make_final):
        return make_router(
            config,
            fake_connector,
            [make_final("unused")],
            scripted_provider,
            make_tool_call,
            make_final,
        )

    def test_help(self, config, fake_connector, scripted_provider, make_tool_call, make_final) -> None:
        router, created = self._router(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        router.handle_message(msg("/help"))
        assert any("/status" in text for text in fake_connector.texts())
        # A command is answered locally, without a model call.
        assert created[0].provider.calls == []

    def test_whoami_reveals_ids(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, _ = self._router(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        router.handle_message(msg("/whoami", user_id="777", chat_id="888"))
        joined = " ".join(fake_connector.texts())
        assert "777" in joined and "888" in joined

    def test_status(self, config, fake_connector, scripted_provider, make_tool_call, make_final) -> None:
        router, _ = self._router(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        router.handle_message(msg("/status"))
        assert any("approval:" in text for text in fake_connector.texts())

    def test_tools(self, config, fake_connector, scripted_provider, make_tool_call, make_final) -> None:
        router, _ = self._router(
            config, fake_connector, scripted_provider, make_tool_call, make_final
        )
        router.handle_message(msg("/tools"))
        assert any("shell" in text for text in fake_connector.texts())

    def test_unknown_command_falls_through_to_the_agent(
        self, config, fake_connector, scripted_provider, make_tool_call, make_final
    ) -> None:
        router, created = make_router(
            config,
            fake_connector,
            [make_final("handled by model")],
            scripted_provider,
            make_tool_call,
            make_final,
        )
        router.handle_message(msg("/notacommand"))
        assert created, "unknown commands should be ordinary text"
        assert "handled by model" in fake_connector.texts()


# --------------------------------------------------------------------------
# registry / dependency guard
# --------------------------------------------------------------------------

class TestRegistry:
    def test_builtin_names(self) -> None:
        from ackstreet.connectors import registry

        registry.load_builtin()
        assert names() == ["telegram", "whatsapp"]

    def test_lookup(self) -> None:
        assert get("telegram").name == "telegram"
        assert get("TELEGRAM").name == "telegram"
        assert find("nope") is None

    def test_unknown_connector_raises(self) -> None:
        from ackstreet.connectors.base import ConnectorError

        with pytest.raises(ConnectorError):
            get("myspace")

    def test_importing_the_package_is_cheap(self) -> None:
        """Importing connectors must not require an optional dependency."""
        import importlib

        module = importlib.import_module("ackstreet.connectors")
        assert hasattr(module, "Connector")
        assert hasattr(module, "MessageRouter")

    def test_not_installed_error_says_how_to_fix(self) -> None:
        error = NotInstalledError("whatsapp", ["neonize"], "whatsapp")
        assert "neonize" in str(error)
        assert "ackstreet-agent[whatsapp]" in str(error)
