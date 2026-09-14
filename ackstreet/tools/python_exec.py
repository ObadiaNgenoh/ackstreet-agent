"""Python code execution tool.

Runs a snippet in a *separate child interpreter* rather than in-process. That
keeps the agent's own memory state safe: a crash, an infinite loop, or a
``sys.exit`` inside generated code cannot take down the running agent.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..config import Config
from .base import Tool, ToolResult


class PythonExecTool(Tool):
    name = "python"
    description = (
        "Execute a Python snippet in a fresh interpreter and return stdout and stderr. "
        "Use for calculations, data wrangling, quick scripts and verifying code. "
        "State does not persist between calls, so write files if you need to carry data over."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute."},
            "timeout": {"type": "integer", "description": "Seconds before execution is killed (default 60)."},
        },
        "required": ["code"],
    }
    dangerous = True

    def run(self, code: str, timeout: int | None = None, **_: Any) -> ToolResult:
        if not self.config.get("tools", "allow_python", True):
            return ToolResult.failure("python execution is disabled (tools.allow_python = false)")

        if not code or not code.strip():
            return ToolResult.failure("code must not be empty")

        limit = int(timeout or self.config.get("tools", "shell_timeout", 60))
        workspace = self.config.workspace
        workspace.mkdir(parents=True, exist_ok=True)

        script_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", dir=str(workspace), delete=False, encoding="utf-8"
            ) as handle:
                handle.write(code)
                script_path = Path(handle.name)

            completed = subprocess.run(
                [sys.executable, "-u", str(script_path)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"python execution timed out after {limit}s")
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)

        stdout = (completed.stdout or "").rstrip()
        stderr = (completed.stderr or "").rstrip()

        sections = [f"exit_code: {completed.returncode}"]
        if stdout:
            sections.append(f"stdout:\n{stdout}")
        if stderr:
            sections.append(f"stderr:\n{stderr}")
        if not stdout and not stderr:
            sections.append("(no output)")

        body = "\n".join(sections)
        if completed.returncode != 0:
            return ToolResult.failure(body, exit_code=completed.returncode)
        return ToolResult.success(body, exit_code=0)


__all__ = ["PythonExecTool"]
