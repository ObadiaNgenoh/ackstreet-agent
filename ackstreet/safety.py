"""Approval gate for dangerous tools.

The agent can run shell commands, overwrite files and execute Python. Without a
gate, a single bad model turn (or a prompt-injection hidden in a fetched web
page) is enough to do real damage. This module decides whether each dangerous
tool call runs immediately, needs an explicit human "yes", or is refused.

Three modes, set with ``agent.approval_mode``:

``auto``       every dangerous tool runs without asking. This is the default so
               existing setups keep working; ``ackstreet doctor`` warns about it.
``ask``        every dangerous tool call is put to a human first. In a
               non-interactive run (``--yes`` not given, no approving callback)
               the call is **refused**, never silently allowed.
``allowlist``  dangerous calls whose target matches ``agent.approval_allowlist``
               run; everything else needs a human "yes".

``agent.approval_denylist`` is a second layer that applies in **every** mode:
a matching call is refused outright, even under ``auto``, and even if a human
would have said yes. It sits alongside the per-tool command blocklist in
``tools.blocked_commands``, which is screened later inside :class:`ShellTool`.

Allowlist entry grammar (``fnmatch`` patterns)::

    ls                  bare name  -> a shell command starting with `ls`
    git status*         glob       -> shell commands matching the glob
    read_file           bare name  -> that whole tool is allowed
    read_file:docs/*    tool:glob  -> that tool, when the target matches
    python:print(*)     tool:glob  -> the code argument matches

The *target* an entry is matched against depends on the tool: the command for
``shell``, the path for the file tools, the code for ``python``, and the skill
name for the skill tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, Callable, Dict, Optional, Tuple

from .config import Config

#: Valid values for ``agent.approval_mode``.
MODES: Tuple[str, ...] = ("auto", "ask", "allowlist")

#: Tools whose target is a filesystem path.
_PATH_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "delete_file",
        "list_directory",
        "search_files",
    }
)

#: Tools whose target is a skill name.
_SKILL_TOOLS = frozenset({"save_skill", "update_skill"})


@dataclass
class ApprovalRequest:
    """What the human is being asked to approve."""

    tool: str
    target: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    mode: str = "ask"

    def describe(self) -> str:
        """One-line, human-readable summary of the pending action."""
        detail = self.target.strip().replace("\n", " ")
        if len(detail) > 240:
            detail = detail[:237] + "..."
        return f"{self.tool}: {detail}" if detail else self.tool


@dataclass
class ApprovalDecision:
    """The gate's verdict for one tool call."""

    approved: bool
    reason: str = ""
    source: str = "auto"

    #: The value matched against the allow/deny lists, for logging.
    target: str = ""


#: A callback that asks a human. Returns True to run the call.
Approver = Callable[[ApprovalRequest], bool]


def _target_for(tool_name: str, arguments: Dict[str, Any]) -> str:
    """Return the string an allow/deny entry is matched against."""
    if not isinstance(arguments, dict):
        return ""
    if tool_name == "shell":
        return str(arguments.get("command", "") or "")
    if tool_name == "python":
        return str(arguments.get("code", "") or "")
    if tool_name in _PATH_TOOLS:
        return str(arguments.get("path", "") or "")
    if tool_name in _SKILL_TOOLS:
        return str(arguments.get("name", "") or "")
    # Unknown tool: match against every string argument, joined.
    return " ".join(str(v) for v in arguments.values() if isinstance(v, str))


def entry_matches(entry: str, tool_name: str, target: str) -> bool:
    """Does one allow/deny entry cover this tool call?

    See the module docstring for the grammar. Matching is case-insensitive for
    shell-like entries because command casing is not meaningful.
    """
    entry = (entry or "").strip()
    if not entry or not target:
        return False

    if ":" in entry:
        scope, pattern = entry.split(":", 1)
        scope = scope.strip()
        pattern = pattern.strip()
        if not pattern:
            return False
        # A scoped entry names a tool. It matches only that tool.
        if scope == tool_name:
            return fnmatch(target.strip(), pattern)
        # `shell:git status` is a common spelling; accept it too.
        return False

    # Unscoped entry: a bare tool name allows that whole tool.
    if entry == tool_name:
        return True

    # Otherwise treat it as a shell-command pattern. This is the common case:
    # `ls`, `git status`, `pytest *`.
    if tool_name != "shell":
        return False

    command = target.strip()
    lowered = command.lower()
    lowered_entry = entry.lower()

    if fnmatch(lowered, lowered_entry):
        return True
    # Bare names should also cover their own arguments: `ls` allows `ls -la`
    # but must not allow `lsblk`.
    first_token = lowered.split(maxsplit=1)[0] if lowered.split() else ""
    return first_token == lowered_entry or first_token.startswith(lowered_entry + " ")


class ApprovalPolicy:
    """Decides whether a dangerous tool call may run."""

    def __init__(self, config: Config, approver: Optional[Approver] = None) -> None:
        self.config = config
        self.approver = approver

    # -- configuration -----------------------------------------------------

    @property
    def mode(self) -> str:
        raw = str(self.config.get("agent", "approval_mode", "auto") or "auto").strip().lower()
        return raw if raw in MODES else "auto"

    @property
    def allowlist(self) -> Sequence[str]:
        return list(self.config.get("agent", "approval_allowlist", []) or [])

    @property
    def denylist(self) -> Sequence[str]:
        return list(self.config.get("agent", "approval_denylist", []) or [])

    @property
    def interactive(self) -> bool:
        """True when there is somebody able to answer a prompt."""
        return self.approver is not None

    def describe(self) -> str:
        if self.mode == "auto":
            return "auto (dangerous tools run without asking)"
        if self.mode == "ask":
            return "ask (every dangerous tool call needs approval)"
        return f"allowlist ({len(self.allowlist)} rule(s); everything else needs approval)"

    # -- the decision ------------------------------------------------------

    def requires_approval(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        """Return ``(needs_human, why)`` for one call, ignoring the denylist."""
        if self.mode == "auto":
            return False, "approval_mode=auto"

        target = _target_for(tool_name, arguments)
        for entry in self.allowlist:
            if entry_matches(entry, tool_name, target):
                return False, f"matched allowlist rule '{entry}'"

        if self.mode == "allowlist":
            return True, "not in approval_allowlist"
        return True, "approval_mode=ask"

    def review(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        dangerous: bool = True,
    ) -> ApprovalDecision:
        """Full verdict for one tool call, including the denylist and the prompt."""
        target = _target_for(tool_name, arguments)

        # Layer 1: the denylist refuses outright, in every mode.
        for entry in self.denylist:
            if entry_matches(entry, tool_name, target):
                return ApprovalDecision(
                    approved=False,
                    reason=(
                        f"refused: call matches approval_denylist rule '{entry}'. "
                        "Remove the rule from agent.approval_denylist to allow it."
                    ),
                    source="denylist",
                    target=target,
                )

        if not dangerous:
            return ApprovalDecision(True, "tool has no side effects", "read-only", target)

        needs_human, why = self.requires_approval(tool_name, arguments)
        if not needs_human:
            return ApprovalDecision(True, why, "policy", target)

        if self.approver is None:
            return ApprovalDecision(
                approved=False,
                reason=(
                    f"refused: {why}, and no approval channel is available. "
                    "This run is non-interactive. Re-run with --yes to approve "
                    "dangerous tools automatically, or set "
                    "agent.approval_mode = auto, or add an allowlist rule."
                ),
                source="non-interactive",
                target=target,
            )

        request = ApprovalRequest(
            tool=tool_name,
            target=target,
            arguments=arguments if isinstance(arguments, dict) else {},
            reason=why,
            mode=self.mode,
        )
        try:
            granted = bool(self.approver(request))
        except Exception as exc:  # noqa: BLE001 - a broken prompt must deny, not allow
            return ApprovalDecision(
                approved=False,
                reason=f"refused: the approval prompt failed ({type(exc).__name__}: {exc}).",
                source="prompt-error",
                target=target,
            )

        if granted:
            return ApprovalDecision(True, f"approved by the user ({why})", "user", target)
        return ApprovalDecision(
            approved=False,
            reason=(
                f"refused: the user declined this {tool_name} call. Do not retry the "
                "same action; choose a different approach or explain what is blocking you."
            ),
            source="user",
            target=target,
        )


__all__ = [
    "MODES",
    "ApprovalDecision",
    "ApprovalPolicy",
    "ApprovalRequest",
    "Approver",
    "entry_matches",
]
