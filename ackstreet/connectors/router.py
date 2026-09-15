"""Routing: chat message in, agent answer out.

The router is the piece that makes a connector's messages *the same agent* as
the CLI. It owns three concerns:

1. **Authorisation** -- the connector's user allowlist is consulted before the
   agent is ever reached.
2. **Routing** -- accepted text goes through the normal agent loop, so every
   capability (tools, skills, memory, multi-step planning) behaves exactly as
   it does locally.
3. **Approval over chat** -- a dangerous tool call is not silently allowed in a
   messaging context. The router asks the human by *sending a yes/no question
   into the chat* and waits for their reply, which becomes the approval
   decision for the existing :class:`~ackstreet.safety.ApprovalPolicy`.

Nothing here bypasses the gate in :mod:`ackstreet.safety`: the router supplies an
*approver callback*, exactly the mechanism the CLI uses for its terminal
prompt. The deny-list and the command blocklist still apply on top.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..config import Config
from ..safety import ApprovalRequest
from .base import Connector, IncomingMessage
from .sessions import SessionManager

#: Answers that grant approval.
APPROVE_WORDS = frozenset({"y", "yes", "yeah", "yep", "ok", "okay", "approve", "allow", "go"})
#: Answers that refuse.
DENY_WORDS = frozenset({"n", "no", "nope", "deny", "stop", "cancel", "abort"})
#: Answers that grant everything for the rest of the session.
ALWAYS_WORDS = frozenset({"always", "all", "a"})

_ANSWER_RE = re.compile(r"^\s*([a-zA-Z]+)\b")


def classify_answer(text: str) -> Optional[str]:
    """Map a free-text chat reply onto approve / deny / always.

    Only the first word is considered, so ``"yes please go ahead"`` counts as
    approval and ``"no, that is wrong"`` counts as a refusal. Anything else --
    including an unrelated new instruction -- returns None so the caller can
    treat it as a fresh turn instead of an answer.
    """
    match = _ANSWER_RE.match(text or "")
    if not match:
        return None
    word = match.group(1).lower()
    if word in ALWAYS_WORDS:
        return "always"
    if word in APPROVE_WORDS:
        return "approve"
    if word in DENY_WORDS:
        return "deny"
    return None


@dataclass
class PendingApproval:
    """An approval question that has been sent and is awaiting a reply."""

    chat_id: str
    tool: str
    target: str
    created: float = field(default_factory=time.time)
    #: Called with the verdict by :meth:`MessageRouter.resolve_pending`, on the
    #: listener thread. This is what releases the waiter.
    on_answer: Optional[Callable[[bool], None]] = None


@dataclass
class RouteOutcome:
    """What happened to one inbound message."""

    handled: bool
    reason: str = ""
    reply: str = ""
    steps: int = 0
    skills: List[str] = field(default_factory=list)
    elapsed: float = 0.0


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


class MessageRouter:
    """Bridges one connector to one agent-per-chat."""

    def __init__(
        self,
        config: Config,
        connector: Connector,
        agent_factory=None,
        session_ttl: Optional[float] = None,
        max_sessions: int = 100,
        log=None,
        wait_slice: float = 0.05,
        max_workers: int = 4,
    ) -> None:
        self.config = config
        self.connector = connector
        self.log = log or (lambda message: None)
        #: How long to sleep between checks for an in-chat verdict. Tests shrink
        #: this to keep gated tool calls fast.
        self.wait_slice = float(wait_slice)

        # Messages from the listener are handled on worker threads. This matters
        # for correctness, not just throughput: an agent turn that is waiting
        # for an approval prompt blocks its thread, so a single-threaded
        # listener would never read the user's "yes" out of the platform's
        # queue -- a deadlock. The session lock keeps each chat's turns ordered.
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="ackstreet-conn"
        )
        self._futures: List[Any] = []

        def default_factory(_key: str):
            # Imported lazily so tests can inject a fake without the agent's
            # provider stack being built.
            from ..agent import Agent

            return Agent(config)

        self.agents_factory = agent_factory or default_factory

        ttl = session_ttl
        if ttl is None:
            ttl = float(config.get("connectors", "session_ttl", 3600) or 0)
        self.sessions = SessionManager(
            lambda key: self._build_agent(key), ttl=ttl, max_sessions=max_sessions
        )

        self._always: Dict[str, set] = {}
        self._pending: Dict[str, PendingApproval] = {}
        self._lock = threading.RLock()
        self.stats = {"received": 0, "rejected": 0, "handled": 0, "errors": 0}

    # -- agent construction ------------------------------------------------

    def _build_agent(self, key: str):
        """Create the agent for one chat, wired to this chat's approver."""
        agent = self.agents_factory(key)
        # Replace the gate with one whose approver asks *this* chat. The
        # policy object itself (modes, allow/deny lists) is unchanged.
        agent.approvals = self.config.approval_policy(
            approver=self._approver_for(key)
        )
        return agent

    # -- approval over chat ------------------------------------------------

    def _approver_for(self, key: str):
        def approve(request: ApprovalRequest) -> bool:
            return self.request_approval(key, request)

        return approve

    def _timeout_seconds(self) -> float:
        """The approval wait budget, in seconds."""
        return float(self.config.get("connectors", "approval_timeout", 300) or 300)

    def request_approval(self, key: str, request: ApprovalRequest) -> bool:
        """Ask the chat to approve a call and block until they answer.

        A timeout is a refusal. So is the platform failing to deliver the
        question -- never the other way round.
        """
        chat_id = key.split(":", 1)[1] if ":" in key else key

        with self._lock:
            always = self._always.setdefault(key, set())
            if request.tool in always:
                return True

        question = (
            f"Approval needed: the agent wants to run {request.tool}.\n"
            f"  {_truncate(request.target, 300) or '(no arguments)'}\n"
            f"Reply 'yes' to allow once, 'always' to allow {request.tool} for "
            f"this chat, or 'no' to refuse."
        )
        # The reply handler publishes its verdict here, since it runs on the
        # listener thread rather than this one.
        decision: Dict[str, bool] = {"answered": False, "approved": False}

        pending = PendingApproval(
            chat_id=chat_id,
            tool=request.tool,
            target=request.target,
            on_answer=lambda approved: decision.update(answered=True, approved=approved),
        )
        # Register the waiter BEFORE sending the question. Otherwise a user who
        # answers almost instantly -- or a test, or a very fast client -- has
        # their reply arrive while no pending approval exists yet, and it is
        # silently consumed as an ordinary message instead of an answer.
        with self._lock:
            self._pending[key] = pending

        try:
            self.connector.send(chat_id, question)
        except Exception as exc:  # noqa: BLE001 - failing to ask must deny
            with self._lock:
                if self._pending.get(key) is pending:
                    del self._pending[key]
            self.log(f"could not send approval prompt: {exc}")
            return False

        self.log(f"awaiting approval in {key} for {request.tool}")

        timeout = self._timeout_seconds()
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if decision["answered"]:
                    break
                if self.connector.stopping:
                    return False
                if self._pending.get(key) is not pending:
                    # The pending marker was consumed without a verdict (e.g.
                    # the chat was cleared); treat it as a refusal.
                    return decision["approved"]
                time.sleep(self.wait_slice)
        finally:
            with self._lock:
                if self._pending.get(key) is pending:
                    del self._pending[key]

        if decision["answered"]:
            self.log(f"approval for {request.tool} was answered in-chat")
            return decision["approved"]

        self.log(f"approval for {request.tool} timed out after {timeout:.0f}s")
        try:
            self.connector.send(
                chat_id, "No approval received in time, so the action was not taken."
            )
        except Exception:  # noqa: BLE001
            pass
        return False

    def resolve_pending(self, message: IncomingMessage) -> Optional[str]:
        """Consume *message* if it answers a pending approval.

        Returns the verdict (``approve`` / ``deny`` / ``always``) when the
        message was an answer, else None so it is handled as a normal turn.

        All of this happens under the lock, together with the marker removal:
        the waiter thread must not be able to observe the answer and the
        cleared marker in a way that loses the verdict.
        """
        with self._lock:
            pending = self._pending.get(message.session_key)
            if pending is None:
                return None
            if message.chat_id != pending.chat_id:
                return None

            verdict = classify_answer(message.text)
            if verdict is None:
                return None

            if verdict == "always":
                self._always.setdefault(message.session_key, set()).add(pending.tool)

            # Clearing the pending marker unblocks request_approval().
            self._pending.pop(message.session_key, None)

            if pending.on_answer is not None:
                try:
                    pending.on_answer(verdict in ("approve", "always"))
                except Exception:  # noqa: BLE001 - a broken waiter must not crash the bot
                    pass

        self._safe_send(
            message.chat_id,
            {
                "approve": f"Approved. Running {pending.tool}.",
                "always": f"Approved. {pending.tool} will be allowed for this chat.",
                "deny": f"Refused. {pending.tool} will not run.",
            }[verdict],
        )
        return verdict

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # -- commands ----------------------------------------------------------

    def handle_command(self, message: IncomingMessage) -> Optional[str]:
        """Handle a ``/`` command locally. Returns a reply, or None."""
        text = (message.text or "").strip()
        if not text.startswith("/"):
            return None
        command = text.split()[0].lower().lstrip("/")
        agent = self.sessions.get(message.session_key).agent

        if command in ("help", "start"):
            return (
                "I am an ACKSTREET AGENT instance. Send me a task and I will "
                "carry it out with tools.\n\n"
                "/help    this message\n"
                "/status  provider, approval mode, session state\n"
                "/skills  list the skills I have learned\n"
                "/tools   list my tools\n"
                "/clear   forget this chat's context\n"
                "/whoami  show the id I have for you"
            )
        if command == "whoami":
            return (
                f"platform: {message.platform}\n"
                f"chat id: {message.chat_id}\n"
                f"your user id: {message.user_id}"
            )
        if command == "status":
            spec = self.config.resolve_provider()
            session = self.sessions.get(message.session_key)
            return (
                f"provider: {spec.name} ({spec.model or 'model unset'})\n"
                f"approval: {agent.approvals.describe()}\n"
                f"tools: {len(agent.tools.names())}\n"
                f"skills: {len(agent.skills.list())}\n"
                f"session: {session.describe()}"
            )
        if command == "skills":
            skills = agent.skills.list()
            if not skills:
                return "No skills learned yet."
            return "\n".join(f"- {s.name}: {s.description}" for s in skills[:40])
        if command == "tools":
            return ", ".join(agent.tools.names())
        if command == "clear":
            self.sessions.reset(message.session_key)
            return "Conversation cleared. My learned skills are kept."
        # Unknown /command: fall through to the agent as ordinary text.
        return None

    # -- routing -----------------------------------------------------------

    def handle_message(self, message: IncomingMessage) -> RouteOutcome:
        """Process one inbound message end to end."""
        started = time.time()
        self.stats["received"] += 1

        allowed, why = self.connector.authorize(message)
        if not allowed:
            self.stats["rejected"] += 1
            self.log(f"rejected {message.summary()} -- {why}")
            try:
                self.connector.send(message.chat_id, f"Not authorised: {why}")
            except Exception:  # noqa: BLE001
                pass
            return RouteOutcome(False, reason=why)

        # A reply that answers an outstanding approval is consumed here and
        # never becomes a new task.
        if self.resolve_pending(message) is not None:
            return RouteOutcome(True, reason="approval answer", elapsed=time.time() - started)

        command_reply = self.handle_command(message)
        if command_reply is not None:
            self._safe_send(message.chat_id, command_reply)
            self.stats["handled"] += 1
            return RouteOutcome(
                True, reason="command", reply=command_reply, elapsed=time.time() - started
            )

        if not (message.text or "").strip():
            return RouteOutcome(False, reason="empty message")

        session = self.sessions.get(message.session_key)
        self.connector.send_typing(message.chat_id)

        try:
            with session.lock:
                session.touch()
                result = session.agent.chat_turn(message.text, stream=False)
        except Exception as exc:  # noqa: BLE001 - one bad turn must not kill the bot
            self.stats["errors"] += 1
            self.log(f"agent error for {message.session_key}: {type(exc).__name__}: {exc}")
            self._safe_send(
                message.chat_id,
                f"I hit an error handling that: {type(exc).__name__}: {exc}",
            )
            return RouteOutcome(False, reason=str(exc), elapsed=time.time() - started)

        reply = (result.text or "").strip()
        if not reply:
            reply = "(the agent finished without producing an answer)"

        notice = ""
        if result.stopped_reason == "provider_error":
            notice = "[the model call failed -- check the agent's credentials with `ackstreet doctor`]\n\n"
        elif result.stopped_reason == "step_budget_exhausted":
            notice = "[stopped at the step budget; the task may be unfinished]\n\n"

        self._safe_send(message.chat_id, notice + reply)

        if result.skills_learned:
            self._safe_send(
                message.chat_id,
                "[learned skill: " + ", ".join(result.skills_learned) + "]",
            )

        self.stats["handled"] += 1
        return RouteOutcome(
            True,
            reason="handled",
            reply=reply,
            steps=len(result.steps),
            skills=list(result.skills_learned),
            elapsed=time.time() - started,
        )

    def _safe_send(self, chat_id: str, text: str) -> List[str]:
        try:
            return self.connector.send_long(chat_id, text)
        except Exception as exc:  # noqa: BLE001 - delivery failure is logged, not fatal
            self.log(f"failed to send to {chat_id}: {exc}")
            return []

    def dispatch(self, message: IncomingMessage) -> RouteOutcome:
        """Handle *message* synchronously (used by tests and one-off calls)."""
        return self.handle_message(message)

    def dispatch_async(self, message: IncomingMessage) -> Any:
        """Queue *message* for handling on a worker thread.

        This is the callback handed to ``Connector.listen``, so the listener
        returns to polling immediately and a blocking approval prompt cannot
        stop the next message (the user's answer) from being read.
        """
        future = self._pool.submit(self.handle_message, message)
        self._futures.append(future)
        # Keep the bookkeeping list from growing without bound.
        if len(self._futures) > 200:
            self._futures = [f for f in self._futures if not f.done()]
        return future

    def wait_for_idle(self, timeout: Optional[float] = 30.0) -> bool:
        """Block until queued messages are handled. True if all finished."""
        deadline = None if timeout is None else time.time() + timeout
        for future in list(self._futures):
            remaining = None if deadline is None else max(0.0, deadline - time.time())
            try:
                future.result(timeout=remaining)
            except Exception:  # noqa: BLE001 - already reported by handle_message
                pass
        return all(f.done() for f in self._futures)

    def shutdown(self, wait: bool = False) -> None:
        """Stop accepting work and release the worker threads."""
        try:
            self._pool.shutdown(wait=wait, cancel_futures=False)
        except Exception:  # noqa: BLE001
            pass

    def describe(self) -> str:
        return (
            f"{self.connector.name}: {self.sessions.count()} session(s), "
            f"{self.pending_count} pending approval(s), "
            f"{self.stats['received']} received / {self.stats['rejected']} rejected"
        )


__all__ = [
    "ALWAYS_WORDS",
    "APPROVE_WORDS",
    "DENY_WORDS",
    "MessageRouter",
    "PendingApproval",
    "RouteOutcome",
    "classify_answer",
]
