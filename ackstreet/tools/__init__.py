"""Tool layer: discovery, registry, and JSON schemas for the model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # imported lazily at runtime to avoid a circular import
    from ..skills.registry import SkillRegistry

from ..config import Config
from .base import Tool, ToolResult
from .files import (
    DeleteFileTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
)
from .python_exec import PythonExecTool
from .shell import ShellTool
from .web import FetchUrlTool, WebSearchTool


class ToolRegistry:
    """Holds the tools available to the agent and dispatches calls by name."""

    def __init__(self, tools: Optional[List[Tool]] = None) -> None:
        self._tools: Dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    # -- management --------------------------------------------------------

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def tools(self) -> List[Tool]:
        return [self._tools[name] for name in self.names()]

    # -- schemas -----------------------------------------------------------

    def schemas(self) -> List[Dict[str, Any]]:
        return [tool.schema() for tool in self.tools()]

    # -- execution ---------------------------------------------------------

    def execute(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult.failure(
                f"no tool named '{name}'. Available tools: {', '.join(self.names())}"
            )
        return tool.safe_call(arguments)


def build_default_registry(
    config: Config, skill_registry: Optional[SkillRegistry] = None
) -> ToolRegistry:
    """Every tool the agent ships with, honouring config switches.

    Tools whose capability is switched off in the config are simply not
    registered — so the model never sees a tool it cannot use.

    The skills package imports from ``ackstreet.tools.base``, so importing it
    at module scope here would create a cycle. It is imported lazily instead.
    """
    from ..skills.tools import build_skill_tools  # noqa: PLC0415 - avoids cycle

    allow_shell = bool(config.get("tools", "allow_shell", True))
    allow_web = bool(config.get("tools", "allow_web", True))
    allow_python = bool(config.get("tools", "allow_python", True))

    tools: List[Tool] = []

    # Filesystem — always available; the agent is useless without it.
    tools.extend(
        [
            ReadFileTool(config),
            WriteFileTool(config),
            EditFileTool(config),
            ListDirectoryTool(config),
            SearchFilesTool(config),
            DeleteFileTool(config),
        ]
    )

    if allow_shell:
        tools.append(ShellTool(config))
    if allow_web:
        tools.extend([WebSearchTool(config), FetchUrlTool(config)])
    if allow_python:
        tools.append(PythonExecTool(config))

    if skill_registry is not None and config.get("skills", "enabled", True):
        tools.extend(build_skill_tools(config, skill_registry))

    return ToolRegistry(tools)


__all__ = [
    "DeleteFileTool",
    "EditFileTool",
    "FetchUrlTool",
    "ListDirectoryTool",
    "PythonExecTool",
    "ReadFileTool",
    "SearchFilesTool",
    "ShellTool",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "WebSearchTool",
    "WriteFileTool",
    "build_default_registry",
]
