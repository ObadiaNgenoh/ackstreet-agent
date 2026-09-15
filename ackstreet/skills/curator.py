"""Skill curator — the self-improvement loop.

After a session that took real work, the agent is asked to reflect: *what did I
just do that I would want to do the same way next time?* If it proposes a
procedure, the curator writes it into the skills directory so future sessions
load it automatically.

Two safeguards keep this from turning into noise:

* a **floor** — sessions shorter than ``skills.min_steps_to_curate`` tool calls
  are not curated at all;
* a **novelty check** — a proposal whose slug already exists is offered as an
  *update* rather than allowed to create a near-duplicate skill.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..providers.base import BaseProvider, Message
from .registry import Skill, SkillRegistry, slugify

CURATOR_SYSTEM = """\
You are the skill curator for a self-hosted AI agent.

Your job: read a finished work session and decide whether it contains a REUSABLE
procedure worth saving as a skill for future sessions.

A skill is worth saving when ALL of these are true:
  * the session completed a real, repeatable task (not a one-off question);
  * the steps would apply again to a similar task;
  * you can state concrete commands, paths, or tool sequences.

Do NOT propose a skill when the session was:
  * a simple lookup, greeting, or single tool call;
  * a failure with no working resolution;
  * so specific to one file or one datum that it will never recur.

If a skill is warranted, call `propose_skill` exactly once with a short kebab-case
name, a one-line description, and a body containing these sections:
  ## When to Use
  ## Steps   (numbered, with real commands)
  ## Pitfalls
  ## Verification

If no skill is warranted, reply with the single word: NONE
"""

PROPOSE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "propose_skill",
        "description": "Save a reusable procedure as a skill for future sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short kebab-case slug, e.g. 'deploy-static-site'.",
                },
                "description": {
                    "type": "string",
                    "description": "One line: what it does AND when to use it. Include trigger words.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 lowercase tags for filtering.",
                },
                "body": {
                    "type": "string",
                    "description": "Markdown body with ## When to Use / ## Steps / ## Pitfalls / ## Verification.",
                },
            },
            "required": ["name", "description", "body"],
        },
    },
}


@dataclass
class CurationOutcome:
    """Result of one curation pass."""

    created: bool = False
    updated: bool = False
    skill: Optional[Skill] = None
    reason: str = ""

    @property
    def changed(self) -> bool:
        return self.created or self.updated


class SkillCurator:
    """Turns a finished session into a saved skill."""

    def __init__(self, registry: SkillRegistry, provider: Optional[BaseProvider]) -> None:
        self.registry = registry
        self.provider = provider

    # -- transcript rendering ---------------------------------------------

    @staticmethod
    def render_transcript(
        task: str, steps: Sequence[Dict[str, Any]], max_chars: int = 12000
    ) -> str:
        """Flatten the session into a compact text transcript."""
        lines = [f"TASK: {task}", "", "STEPS TAKEN:"]
        for index, step in enumerate(steps, start=1):
            name = step.get("tool") or step.get("name") or "?"
            arguments = step.get("arguments") or {}
            summary = step.get("summary") or ""
            rendered_args = json.dumps(arguments, ensure_ascii=False)[:600]
            lines.append(f"{index}. {name}({rendered_args})")
            if summary:
                lines.append(f"   -> {summary[:600]}")
        transcript = "\n".join(lines)
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n... [transcript truncated]"
        return transcript

    # -- proposal parsing --------------------------------------------------

    @staticmethod
    def parse_proposal(text: str, tool_calls: Sequence[Any]) -> Optional[Dict[str, Any]]:
        """Extract a proposal from a tool call, falling back to a JSON body."""
        for call in tool_calls:
            arguments = getattr(call, "arguments", None)
            if arguments is None:
                continue
            name = getattr(call, "name", "")
            if name != "propose_skill":
                continue
            if "__parse_error__" in arguments:
                # Try to salvage the raw payload.
                raw = arguments.get("__raw__", "")
                try:
                    arguments = json.loads(raw)
                except Exception:  # noqa: BLE001
                    return None
            if arguments.get("name") and arguments.get("body"):
                return dict(arguments)

        # Some models answer with a JSON object in plain text instead.
        stripped = (text or "").strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict) and data.get("name") and data.get("body"):
                    return data
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def is_declined(text: str) -> bool:
        return (text or "").strip().upper().startswith("NONE")

    # -- main entry point --------------------------------------------------

    def curate(
        self,
        task: str,
        steps: Sequence[Dict[str, Any]],
        summary: str = "",
        min_steps: int = 3,
        force: bool = False,
    ) -> CurationOutcome:
        """Decide whether the session is worth saving, and save it if so."""
        if self.provider is None:
            return CurationOutcome(reason="no provider configured; curation skipped")

        if not self.registry.dir.exists():
            self.registry.ensure_dir()

        if len(steps) < min_steps and not force:
            return CurationOutcome(
                reason=f"session had only {len(steps)} step(s); below the "
                f"curation floor of {min_steps}"
            )

        transcript = self.render_transcript(task, steps)
        existing = self.registry.list()
        existing_block = (
            "\n".join(f"- {s.name}: {s.description}" for s in existing)
            or "(none yet)"
        )

        prompt = (
            f"{transcript}\n\n"
            f"SESSION SUMMARY:\n{summary or '(none provided)'}\n\n"
            f"SKILLS THAT ALREADY EXIST (do not duplicate these):\n{existing_block}\n"
        )

        try:
            response = self.provider.chat(
                messages=[
                    Message(role="system", content=CURATOR_SYSTEM),
                    Message(role="user", content=prompt),
                ],
                tools=[PROPOSE_TOOL],
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001 - curation must never break a run
            return CurationOutcome(reason=f"curator model call failed: {exc}")

        if self.is_declined(response.text) and not response.tool_calls:
            return CurationOutcome(reason="curator judged this session not worth saving")

        proposal = self.parse_proposal(response.text, response.tool_calls)
        if not proposal:
            return CurationOutcome(reason="curator returned no usable proposal")

        return self.commit(proposal)

    def commit(self, proposal: Dict[str, Any]) -> CurationOutcome:
        """Persist a validated proposal, updating instead of duplicating."""
        name = slugify(str(proposal.get("name", "")))
        body = str(proposal.get("body", "")).strip()
        description = str(proposal.get("description", "")).strip()
        tags = proposal.get("tags") or []

        if not name or not body:
            return CurationOutcome(reason="proposal was missing a name or body")

        existing = self.registry.get(name)
        if existing is not None:
            # Merge rather than fork: keep the original creation date.
            merged = self._merge_bodies(existing.body, body)
            skill = self.registry.update(
                existing.name,
                body=merged,
                description=description or existing.description,
                tags=list(existing.tags) + [t for t in tags if t not in existing.tags],
            )
            return CurationOutcome(updated=True, skill=skill, reason=f"updated skill '{skill.name}'")

        skill = self.registry.create(
            name=name,
            description=description,
            body=body,
            tags=tags,
            source="curator",
        )
        return CurationOutcome(created=True, skill=skill, reason=f"saved new skill '{skill.name}'")

    @staticmethod
    def _merge_bodies(previous: str, addition: str, max_chars: int = 20000) -> str:
        """Append only the genuinely new lines to an existing skill body."""
        known = {line.strip().lower() for line in previous.splitlines() if line.strip()}
        fresh: List[str] = []
        for line in addition.splitlines():
            stripped = line.strip().lower()
            if stripped and stripped in known:
                continue
            fresh.append(line)
        if not fresh:
            return previous
        merged = f"{previous.rstrip()}\n\n## Update ({_today()})\n\n" + "\n".join(fresh).strip()
        return merged[:max_chars]


def _today() -> str:
    import datetime as _dt

    return _dt.date.today().isoformat()


__all__ = ["CURATOR_SYSTEM", "CurationOutcome", "PROPOSE_TOOL", "SkillCurator"]
