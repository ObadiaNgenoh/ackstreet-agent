"""Per-chat conversation sessions.

Each chat gets its own :class:`~ackstreet.agent.Agent`, and therefore its own
message history, its own learned-skill view and its own approval state. Two
people messaging the same bot never see each other's conversation.

Sessions are held in memory. The underlying transcript still reaches disk
through the normal memory store, so a restart loses the live context but not
the record of what happened.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional


class ChatSession:
    """One chat's agent plus the bookkeeping around it."""

    def __init__(self, key: str, agent, connector: str = "") -> None:
        self.key = key
        self.agent = agent
        self.connector = connector
        self.chat_id = key.split(":", 1)[1] if ":" in key else key
        self.created = time.time()
        self.last_active = self.created
        self.turns = 0
        #: Serialises turns for this chat, so two rapid messages cannot
        #: interleave tool calls inside one conversation.
        self.lock = threading.RLock()

    def touch(self) -> None:
        self.last_active = time.time()
        self.turns += 1

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_active

    def describe(self) -> str:
        return (
            f"{self.key}: {self.turns} turn(s), "
            f"idle {self.idle_seconds:.0f}s, "
            f"{len(self.agent.messages)} message(s) in context"
        )


class SessionManager:
    """Creates and tracks one :class:`ChatSession` per chat."""

    def __init__(
        self,
        factory: Callable[[str], object],
        ttl: float = 3600.0,
        max_sessions: int = 100,
    ) -> None:
        self.factory = factory
        self.ttl = float(ttl)
        self.max_sessions = int(max_sessions)
        self._sessions: Dict[str, ChatSession] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> ChatSession:
        """Return the session for *key*, creating it on first use."""
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                return session
            connector = key.split(":", 1)[0] if ":" in key else ""
            session = ChatSession(key, self.factory(key), connector=connector)
            self._sessions[key] = session
            self._evict_locked()
            return session

    def peek(self, key: str) -> Optional[ChatSession]:
        """Return an existing session without creating one."""
        with self._lock:
            return self._sessions.get(key)

    def reset(self, key: str) -> bool:
        """Forget a chat's context. Returns True when a session existed."""
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is None:
            return False
        try:
            session.agent.reset()
        except Exception:  # noqa: BLE001 - reset is best-effort
            pass
        return True

    def keys(self) -> List[str]:
        with self._lock:
            return sorted(self._sessions)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def describe(self) -> str:
        with self._lock:
            return f"{len(self._sessions)} active chat session(s)"

    def evict_idle(self) -> List[str]:
        """Drop sessions past their TTL. Returns the keys that were dropped."""
        if self.ttl <= 0:
            return []
        with self._lock:
            stale = [
                key for key, session in self._sessions.items()
                if session.idle_seconds > self.ttl
            ]
            for key in stale:
                self._sessions.pop(key, None)
        return stale

    def _evict_locked(self) -> None:
        """Enforce ``max_sessions`` by dropping the least recently active."""
        if self.max_sessions <= 0 or len(self._sessions) <= self.max_sessions:
            return
        ordered = sorted(self._sessions.items(), key=lambda kv: kv[1].last_active)
        for key, _ in ordered[: len(self._sessions) - self.max_sessions]:
            self._sessions.pop(key, None)


__all__ = ["ChatSession", "SessionManager"]
