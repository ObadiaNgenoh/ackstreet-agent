"""Shell command execution tool."""

from __future__ import annotations

import subprocess
from typing import Any, List

from ..config import Config
from .base import Tool, ToolResult


class ShellTool(Tool):
    """Run a shell command and capture stdout, stderr and exit code."""

    name = "shell"
    description = (
        "Execute a shell command on the host and return its stdout, stderr and exit code. "
        "Use for inspecting the filesystem, running build/test commands, installing packages, "
        "and any CLI task. Commands run from the workspace directory. "
        "Prefer non-interactive flags (e.g. `-y`, `--yes`) so the command cannot hang."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run, e.g. 'ls -la' or 'pytest -q'.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory. Defaults to the workspace.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds before the command is killed. Defaults to the configured shell_timeout.",
            },
        },
        "required": ["command"],
    }
    dangerous = True

    # -- policy ------------------------------------------------------------

    @property
    def blocked(self) -> List[str]:
        patterns = self.config.get("tools", "blocked_commands", []) or []
        return [str(p) for p in patterns]

    def _screen(self, command: str) -> str | None:
        lowered = command.lower()
        for pattern in self.blocked:
            if pattern and pattern.lower() in lowered:
                return (
                    f"refused: command matches the blocked pattern '{pattern}'. "
                    "This guard exists to prevent destructive commands. "
                    "If the action is genuinely intended, edit tools.blocked_commands "
                    "in the config."
                )
        return None

    # -- execution ---------------------------------------------------------

    def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
        **_: Any,
    ) -> ToolResult:
        if not self.config.get("tools", "allow_shell", True):
            return ToolResult.failure(
                "shell execution is disabled (tools.allow_shell = false in config)"
            )

        refusal = self._screen(command)
        if refusal:
            return ToolResult.failure(refusal)

        working_dir = self.resolve_path(cwd, default=self.config.workspace)
        if not working_dir.exists():
            return ToolResult.failure(f"working directory does not exist: {working_dir}")

        limit = int(timeout or self.config.get("tools", "shell_timeout", 60))

        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(working_dir),
                capture_output=True,
                text=True,
                timeout=limit,
            )
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.stdout:
                partial += str(exc.stdout)
            if exc.stderr:
                partial += "\n" + str(exc.stderr)
            return ToolResult.failure(
                f"command timed out after {limit}s (it may be interactive). "
                f"Partial output:\n{partial[-2000:]}",
                timed_out=True,
            )

        stdout = (completed.stdout or "").rstrip()
        stderr = (completed.stderr or "").rstrip()

        sections = [f"$ {command}", f"exit_code: {completed.returncode}"]
        if stdout:
            sections.append(f"stdout:\n{stdout}")
        if stderr:
            sections.append(f"stderr:\n{stderr}")
        if not stdout and not stderr:
            sections.append("(no output)")

        body = "\n".join(sections)
        if completed.returncode != 0:
            return ToolResult.failure(
                f"command exited with code {completed.returncode}\n{body}",
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return ToolResult.success(
            body, exit_code=0, stdout=stdout, stderr=stderr
        )


__all__ = ["ShellTool"]
