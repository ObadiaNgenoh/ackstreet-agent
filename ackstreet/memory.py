"""Persistent memory: session transcripts plus durable facts.

Two stores, both plain JSON on disk so they stay inspectable:

* ``memory/sessions/<id>.json`` — the full transcript of one run
* ``memory/index.json``        — one-line summaries used for cross-session recall
* ``memory/facts.json``        — explicit long-term facts the agent recorded
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import StoreError
from .providers.base import Message


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class Session:
    """One recorded run."""

    id: str
    task: str
    started: str
    ended: str = ""
    summary: str = ""
    messages: List[Message] = field(default_factory=list)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    skills_learned: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "started": self.started,
            "ended": self.ended,
            "summary": self.summary,
            "provider": self.provider,
            "model": self.model,
            "skills_learned": self.skills_learned,
            "steps": self.steps,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        return cls(
            id=data.get("id", ""),
            task=data.get("task", ""),
            started=data.get("started", ""),
            ended=data.get("ended", ""),
            summary=data.get("summary", ""),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
            steps=data.get("steps", []),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            skills_learned=data.get("skills_learned", []),
        )


class MemoryStore:
    """Reads and writes the persistent memory directory."""

    def __init__(self, memory_dir: Path, enabled: bool = True) -> None:
        self.dir = Path(memory_dir)
        self.enabled = enabled

    # -- paths -------------------------------------------------------------

    @property
    def sessions_dir(self) -> Path:
        return self.dir / "sessions"

    @property
    def index_path(self) -> Path:
        return self.dir / "index.json"

    @property
    def facts_path(self) -> Path:
        return self.dir / "facts.json"

    def ensure(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # -- low-level io ------------------------------------------------------

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"cannot read {path}: {exc}") from exc

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            raise StoreError(f"cannot write {path}: {exc}") from exc

    # -- sessions ----------------------------------------------------------

    def new_session(self, task: str, provider: str = "", model: str = "") -> Session:
        session = Session(
            id=_dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            task=task,
            started=_now(),
            provider=provider,
            model=model,
        )
        return session

    def save_session(self, session: Session) -> Optional[Path]:
        if not self.enabled:
            return None
        self.ensure()
        session.ended = session.ended or _now()
        path = self.sessions_dir / f"{session.id}.json"
        self._write_json(path, session.to_dict())

        index = self._read_json(self.index_path, [])
        index = [entry for entry in index if entry.get("id") != session.id]
        index.append(
            {
                "id": session.id,
                "task": session.task[:200],
                "started": session.started,
                "ended": session.ended,
                "summary": (session.summary or "")[:400],
                "provider": session.provider,
                "model": session.model,
                "steps": len(session.steps),
                "skills_learned": session.skills_learned,
                "file": str(path.name),
            }
        )
        # Index only ever holds the most recent N sessions.
        limit = 500
        index = sorted(index, key=lambda e: e.get("started", ""))[-limit:]
        self._write_json(self.index_path, index)
        return path

    def load_session(self, session_id: str) -> Optional[Session]:
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        return Session.from_dict(self._read_json(path, {}))

    def sessions(self) -> List[Dict[str, Any]]:
        """Index entries, newest first."""
        entries = self._read_json(self.index_path, [])
        return sorted(entries, key=lambda e: e.get("started", ""), reverse=True)

    # -- facts -------------------------------------------------------------

    def facts(self) -> List[Dict[str, Any]]:
        return self._read_json(self.facts_path, [])

    def remember(self, fact: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.enabled:
            raise StoreError("memory is disabled (memory.enabled = false)")
        self.ensure()
        entries = self.facts()
        entry = {"fact": fact.strip(), "tags": tags or [], "recorded": _now()}
        entries.append(entry)
        self._write_json(self.facts_path, entries)
        return entry

    def forget(self, index: int) -> bool:
        entries = self.facts()
        if 0 <= index < len(entries):
            entries.pop(index)
            self._write_json(self.facts_path, entries)
            return True
        return False

    # -- recall ------------------------------------------------------------

    @staticmethod
    def _tokens(text: str) -> set[str]:
        cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
        return {t for t in cleaned.split() if len(t) > 2}

    def recall(self, query: str, limit: int = 8) -> str:
        """Return a compact block of relevant past work.

        Uses token-overlap scoring rather than embeddings so it works offline
        with no extra dependency and no API call.
        """
        if not self.enabled:
            return ""

        query_tokens = self._tokens(query)
        if not query_tokens:
            return ""

        scored: List[tuple[float, Dict[str, Any]]] = []

        for entry in self.sessions():
            haystack = f"{entry.get('task', '')} {entry.get('summary', '')}"
            overlap = len(query_tokens & self._tokens(haystack))
            if overlap:
                scored.append((float(overlap), {"type": "session", **entry}))

        for fact in self.facts():
            overlap = len(query_tokens & self._tokens(fact.get("fact", "")))
            if overlap:
                scored.append((float(overlap) + 0.5, {"type": "fact", **fact}))

        if not scored:
            return ""

        scored.sort(key=lambda pair: pair[0], reverse=True)
        lines = ["## Relevant context from earlier sessions", ""]
        for _, entry in scored[:limit]:
            if entry["type"] == "session":
                learned = entry.get("skills_learned") or []
                suffix = f" (skills: {', '.join(learned)})" if learned else ""
                lines.append(
                    f"- [{entry.get('started', '')[:10]}] {entry.get('task', '')[:140]}"
                    f" — {entry.get('summary', '(no summary)')[:200]}{suffix}"
                )
            else:
                lines.append(
                    f"- [fact, {entry.get('recorded', '')[:10]}] {entry.get('fact', '')}"
                )
        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        sessions = self.sessions()
        facts = self.facts()
        return {
            "sessions": len(sessions),
            "facts": len(facts),
            "last_session": sessions[0].get("started") if sessions else None,
            "dir": str(self.dir),
        }


__all__ = ["MemoryStore", "Session"]
