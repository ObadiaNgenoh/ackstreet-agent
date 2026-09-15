"""The agent loop: plan, act, observe, repeat, then reflect.

The loop is deliberately small and synchronous. Each iteration:

1. send the conversation plus tool schemas to the provider;
2. if the model asked for tools, execute them and append the results;
3. if it produced prose instead, that is the answer — stop.

Guard rails that matter in practice:

* **step budget** — never loop forever (``agent.max_steps``);
* **repeat detection** — an identical (tool, args) call three times in a row
  means the model is stuck; the loop interrupts and tells it so;
* **context trimming** — old tool output is dropped first, so long sessions
  stay inside the model's window.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .errors import ConfigError, ProviderError
from .memory import MemoryStore
from .providers import provider_from_config
from .providers.base import BaseProvider, Message, ProviderResponse
from .safety import ApprovalPolicy, Approver
from .skills.curator import SkillCurator
from .skills.registry import SkillRegistry
from .tools import ToolRegistry, ToolResult, build_default_registry

EventCallback = Callable[["AgentEvent"], None]

REPEAT_LIMIT = 3


@dataclass
class AgentEvent:
    """Progress notification emitted while the agent works."""

    type: str  # plan | thinking | text | tool_start | tool_end | memory | skill | done | error
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """Everything a finished run produced."""

    text: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    skills_learned: List[str] = field(default_factory=list)
    stopped_reason: str = "completed"
    plan: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def tool_calls(self) -> int:
        return len(self.steps)


class Agent:
    """A configured agent instance."""

    def __init__(
        self,
        config: Config,
        provider: Optional[BaseProvider] = None,
        tools: Optional[ToolRegistry] = None,
        skills: Optional[SkillRegistry] = None,
        memory: Optional[MemoryStore] = None,
        on_event: Optional[EventCallback] = None,
        approver: Optional[Approver] = None,
        approval_mode: Optional[str] = None,
    ) -> None:
        self.config = config
        self.config.ensure_dirs()
        if approval_mode:
            config.set("agent", "approval_mode", approval_mode)
        self.approvals = ApprovalPolicy(config, approver=approver)

        self.skills = skills or SkillRegistry(
            config.skills_dir,
            seeds_dir=_packaged_seeds_dir(),
        )
        self.memory = memory or MemoryStore(
            config.memory_dir, enabled=bool(config.get("memory", "enabled", True))
        )

        self._provider = provider
        self.provider_name = config.get("agent", "provider", "openai")
        self.tools = tools or build_default_registry(config, self.skills)
        self.on_event = on_event

        self.messages: List[Message] = []
        self.steps: List[Dict[str, Any]] = []

    # -- provider ----------------------------------------------------------

    @property
    def provider(self) -> BaseProvider:
        """Lazily build the provider so ``--help`` works without a key."""
        if self._provider is None:
            spec = self.config.resolve_provider()
            if not spec.model:
                raise ConfigError(
                    f"no model configured for provider '{spec.name}'. "
                    "Set agent.model or providers.<name>.model in the config."
                )
            self._provider = provider_from_config(self.config)
            self.provider_name = spec.name
        return self._provider

    # -- events ------------------------------------------------------------

    def emit(self, event_type: str, **data: Any) -> None:
        if self.on_event is not None:
            try:
                self.on_event(AgentEvent(type=event_type, data=data))
            except Exception:  # noqa: BLE001 - a UI callback must not break the run
                pass

    # -- approval gate -----------------------------------------------------

    def _execute_with_approval(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Run a tool call, consulting the approval gate for dangerous tools.

        A refusal comes back as a failed :class:`ToolResult`, so the model sees
        it as an ordinary error it can adapt to rather than a crash.
        """
        tool = self.tools.get(name)
        dangerous = tool is not None and bool(getattr(tool, "dangerous", False))
        decision = self.approvals.review(name, arguments, dangerous=dangerous)

        self.emit(
            "approval",
            tool=name,
            approved=decision.approved,
            source=decision.source,
            reason=decision.reason,
            target=decision.target,
        )
        if not decision.approved:
            return ToolResult.failure(
                decision.reason,
                approval="denied",
                approval_source=decision.source,
            )
        return self.tools.execute(name, arguments)

    # -- prompting ---------------------------------------------------------

    def build_system_prompt(self, task: str = "") -> str:
        """System prompt = base instructions + skills index + recalled memory."""
        parts = [self.config.system_prompt().strip()]

        if self.config.get("agent", "auto_load_skill", True) and self.config.get(
            "skills", "enabled", True
        ):
            index = self.skills.render_index()
            if index:
                parts.append(index)

        recall = self.memory.recall(
            task, limit=int(self.config.get("memory", "recall_limit", 8))
        ) if self.config.get("memory", "enabled", True) and task else ""
        if recall:
            parts.append(recall)

        parts.append(
            "## Environment\n"
            f"- Working directory: {self.config.workspace}\n"
            f"- Skills directory: {self.config.skills_dir}\n"
            f"- Tools available: {', '.join(self.tools.names())}\n"
        )
        return "\n\n".join(p for p in parts if p.strip())

    # -- planning ----------------------------------------------------------

    def plan(self, task: str) -> List[str]:
        """Ask the model for a short numbered plan. Best-effort, never fatal."""
        instruction = (
            "Break the following task into a short numbered plan. "
            "Reply with only the numbered steps, one per line, no preamble.\n\n"
            f"TASK: {task}"
        )
        try:
            response = self.provider.chat(
                messages=[
                    Message(role="system", content=self.config.system_prompt()),
                    Message(role="user", content=instruction),
                ],
                tools=None,
                temperature=0.1,
            )
        except ProviderError as exc:
            self.emit("error", message=f"planning skipped: {exc}")
            return []

        steps: List[str] = []
        for line in (response.text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Accept "1.", "1)", "- " and "* " prefixes.
            for prefix in ("- ", "* "):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):].strip()
                    break
            cleaned = stripped.lstrip("0123456789").lstrip(".)").strip()
            if cleaned:
                steps.append(cleaned)
        return steps[:12]

    # -- context management ------------------------------------------------

    def _trim_context(self, messages: List[Message], keep: int) -> List[Message]:
        """Drop the oldest non-system turns, preferring stale tool output."""
        system = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        if len(rest) <= keep:
            return system + rest

        dropped = rest[: len(rest) - keep]
        kept = rest[len(rest) - keep :]

        notice = Message(
            role="user",
            content=(
                f"[{len(dropped)} earlier message(s) were trimmed from the context "
                "to stay within the model window. Re-read files if you need their content.]"
            ),
        )
        return system + [notice] + kept

    # -- the loop ----------------------------------------------------------

    def run(
        self,
        task: str,
        max_steps: Optional[int] = None,
        plan_first: bool = False,
        stream: bool = False,
        curate: Optional[bool] = None,
        history: Optional[Sequence[Message]] = None,
    ) -> RunResult:
        """Execute *task* to completion and return everything it produced."""
        started = time.time()
        budget = int(max_steps or self.config.get("agent", "max_steps", 25))
        temperature = float(self.config.get("agent", "temperature", 0.2))
        keep = int(self.config.get("agent", "context_messages", 40))

        self.steps = []
        session = self.memory.new_session(
            task, provider=self.provider_name, model=self.provider.model
        )

        plan: List[str] = []
        if plan_first:
            plan = self.plan(task)
            if plan:
                self.emit("plan", steps=plan)

        system_prompt = self.build_system_prompt(task)
        self.messages = [Message(role="system", content=system_prompt)]
        if history:
            self.messages.extend(history)
            self.emit("memory", note=f"loaded {len(history)} message(s) from history")
        self.messages.append(Message(role="user", content=task))

        result = RunResult(session_id=session.id, plan=plan)
        stopped_reason = "completed"
        final_text = ""
        repeat_signature = ""
        repeat_count = 0
        stalled = False

        for step_index in range(1, budget + 1):
            context = self._trim_context(self.messages, keep)

            stream_callback = None
            if stream:
                def stream_callback(chunk: str, _step: int = step_index) -> None:  # type: ignore[misc]
                    self.emit("text", chunk=chunk, step=_step)

            self.emit("thinking", step=step_index, total=budget)

            try:
                response: ProviderResponse = self.provider.chat(
                    messages=context,
                    tools=self.tools.schemas(),
                    temperature=temperature,
                    stream_callback=stream_callback,
                )
            except ProviderError as exc:
                stopped_reason = "provider_error"
                final_text = f"The model call failed: {exc}"
                self.emit("error", message=str(exc))
                break

            if response.usage:
                result.usage = response.usage

            self.messages.append(response.to_message())

            # -- no tool calls: this is the answer -------------------------
            if not response.tool_calls:
                final_text = response.text.strip()
                if not final_text:
                    stopped_reason = "empty_response"
                    final_text = "The model returned no content."
                break

            if response.text.strip():
                self.emit("text", chunk=response.text, step=step_index)

            # -- execute every requested tool ------------------------------
            for call in response.tool_calls:
                signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
                if signature == repeat_signature:
                    repeat_count += 1
                else:
                    repeat_signature, repeat_count = signature, 1

                self.emit("tool_start", tool=call.name, arguments=call.arguments, step=step_index)
                tool = self.tools.get(call.name)

                if repeat_count >= REPEAT_LIMIT:
                    note = (
                        f"You have called `{call.name}` with these exact arguments "
                        f"{repeat_count} times without progress. Stop repeating it: change "
                        "your approach, use a different tool, or explain what is blocking you."
                    )
                    self.emit("tool_end", tool=call.name, ok=False, summary="repeat detected")
                    self.messages.append(
                        Message(
                            role="tool",
                            content=f"ERROR: {note}",
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                    self.steps.append(
                        {
                            "step": step_index,
                            "tool": call.name,
                            "arguments": call.arguments,
                            "ok": False,
                            "summary": "repeat detected",
                        }
                    )
                    stopped_reason = "repeat_detected"
                    final_text = (
                        f"Stopped: an identical `{call.name}` call was repeated "
                        f"{repeat_count} times without progress. The loop was interrupted "
                        "so the approach can be changed rather than retried."
                    )
                    stalled = True
                    break

                result_obj = self._execute_with_approval(call.name, call.arguments)
                rendered = result_obj.render(tool.output_limit if tool else 20000)

                self.messages.append(
                    Message(
                        role="tool",
                        content=rendered,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
                self.steps.append(
                    {
                        "step": step_index,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "ok": result_obj.ok,
                        "summary": (result_obj.output if result_obj.ok else result_obj.error)[:2000],
                        "metadata": result_obj.metadata,
                    }
                )
                self.emit(
                    "tool_end",
                    tool=call.name,
                    ok=result_obj.ok,
                    summary=(result_obj.output if result_obj.ok else result_obj.error)[:400],
                )

            if stalled:
                break
        else:
            stopped_reason = "step_budget_exhausted"
            final_text = (
                f"Stopped after reaching the step budget of {budget}. "
                "Increase agent.max_steps or narrow the task."
            )

        # -- record the session -------------------------------------------
        session.ended = ""
        session.steps = self.steps
        session.summary = final_text[:800]
        session.messages = self.messages[-40:]
        session.skills_learned = []
        self.memory.save_session(session)

        # -- reflection / self-improvement --------------------------------
        should_curate = (
            bool(self.config.get("agent", "auto_curate", True)) if curate is None else curate
        )
        if should_curate and self.steps and stopped_reason != "provider_error":
            floor = int(self.config.get("skills", "min_steps_to_curate", 3))
            curator = SkillCurator(self.skills, self.provider)
            outcome = curator.curate(
                task=task, steps=self.steps, summary=final_text, min_steps=floor
            )
            if outcome.changed and outcome.skill is not None:
                result.skills_learned.append(outcome.skill.name)
                session.skills_learned = list(result.skills_learned)
                self.memory.save_session(session)
                self.emit(
                    "skill",
                    action="updated" if outcome.updated else "created",
                    name=outcome.skill.name,
                    description=outcome.skill.description,
                )
            else:
                self.emit("skill", action="none", reason=outcome.reason)

        result.text = final_text
        result.steps = self.steps
        result.stopped_reason = stopped_reason
        result.elapsed = time.time() - started

        self.emit("done", stopped_reason=stopped_reason, steps=len(self.steps))
        return result

    # -- conversation ------------------------------------------------------

    def chat_turn(self, message: str, stream: bool = True) -> RunResult:
        """One turn of an interactive chat, keeping history across turns."""
        temperature = float(self.config.get("agent", "temperature", 0.2))
        keep = int(self.config.get("agent", "context_messages", 40))

        if not self.messages:
            self.messages = [
                Message(role="system", content=self.build_system_prompt(message))
            ]

        self.messages.append(Message(role="user", content=message))
        started = time.time()
        steps: List[Dict[str, Any]] = []
        final_text = ""
        repeat_signature = ""
        repeat_count = 0
        budget = int(self.config.get("agent", "max_steps", 25))
        stopped_reason = "completed"

        for step_index in range(1, budget + 1):
            context = self._trim_context(self.messages, keep)

            stream_callback = None
            if stream:
                def stream_callback(chunk: str, _step: int = step_index) -> None:  # type: ignore[misc]
                    self.emit("text", chunk=chunk, step=_step)

            self.emit("thinking", step=step_index, total=budget)

            try:
                response = self.provider.chat(
                    messages=context,
                    tools=self.tools.schemas(),
                    temperature=temperature,
                    stream_callback=stream_callback,
                )
            except ProviderError as exc:
                self.emit("error", message=str(exc))
                return RunResult(
                    text=f"The model call failed: {exc}",
                    stopped_reason="provider_error",
                    elapsed=time.time() - started,
                )

            self.messages.append(response.to_message())

            if not response.tool_calls:
                final_text = response.text.strip()
                break

            for call in response.tool_calls:
                signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
                if signature == repeat_signature:
                    repeat_count += 1
                else:
                    repeat_signature, repeat_count = signature, 1

                self.emit("tool_start", tool=call.name, arguments=call.arguments, step=step_index)

                if repeat_count >= REPEAT_LIMIT:
                    self.messages.append(
                        Message(
                            role="tool",
                            content="ERROR: repeated identical call — change approach.",
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                    continue

                outcome = self._execute_with_approval(call.name, call.arguments)
                tool = self.tools.get(call.name)
                self.messages.append(
                    Message(
                        role="tool",
                        content=outcome.render(tool.output_limit if tool else 20000),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
                steps.append(
                    {
                        "step": step_index,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "ok": outcome.ok,
                        "summary": (outcome.output if outcome.ok else outcome.error)[:2000],
                    }
                )
                self.emit(
                    "tool_end",
                    tool=call.name,
                    ok=outcome.ok,
                    summary=(outcome.output if outcome.ok else outcome.error)[:400],
                )
        else:
            stopped_reason = "step_budget_exhausted"

        return RunResult(
            text=final_text,
            steps=steps,
            stopped_reason=stopped_reason,
            elapsed=time.time() - started,
        )

    def reset(self) -> None:
        self.messages = []


def _packaged_seeds_dir():
    """Starter skills bundled with the package, if present."""
    from pathlib import Path

    candidate = Path(__file__).parent / "seeds" / "skills"
    return candidate if candidate.exists() else None


__all__ = ["Agent", "AgentEvent", "EventCallback", "RunResult"]
