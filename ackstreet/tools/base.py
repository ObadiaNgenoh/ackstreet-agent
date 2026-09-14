"""Tool abstraction.

A tool advertises a JSON schema, validates its own arguments, and returns a
:class:`ToolResult`. Tools never raise for expected failures — they return
``ok=False`` with a message the model can read and act on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Config


@dataclass
class ToolResult:
    """Outcome of one tool call."""

    ok: bool
    output: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, output: str, **metadata: Any) -> "ToolResult":
        return cls(ok=True, output=output, metadata=metadata)

    @classmethod
    def failure(cls, error: str, **metadata: Any) -> "ToolResult":
        return cls(ok=False, output=f"ERROR: {error}", error=error, metadata=metadata)

    def render(self, limit: int = 20000) -> str:
        """Text handed back to the model."""
        text = self.output if self.ok else f"ERROR: {self.error}"
        if len(text) > limit:
            head = text[: limit // 2]
            tail = text[-limit // 2 :]
            omitted = len(text) - limit
            text = f"{head}\n... [{omitted} characters truncated] ...\n{tail}"
        return text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
        }


class Tool(ABC):
    """Base class for every capability the agent can invoke."""

    #: Name the model uses to call this tool.
    name: str = ""
    #: Shown to the model — be specific about when to use it.
    description: str = ""
    #: JSON Schema (OpenAI ``function.parameters`` shape).
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}
    #: Marks tools with side effects, for logging and future approval gates.
    dangerous: bool = False

    def __init__(self, config: Config) -> None:
        self.config = config

    # -- interface ---------------------------------------------------------

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated keyword arguments."""

    # -- helpers -----------------------------------------------------------

    def schema(self) -> Dict[str, Any]:
        """OpenAI-style function schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def validate(self, arguments: Dict[str, Any]) -> Optional[str]:
        """Return an error string when required arguments are missing."""
        required = self.parameters.get("required") or []
        missing = [key for key in required if key not in arguments]
        if missing:
            return (
                f"missing required argument(s): {', '.join(missing)}. "
                f"Expected schema: {self.parameters}"
            )
        return None

    def safe_call(self, arguments: Dict[str, Any]) -> ToolResult:
        """Validate then run, converting unexpected exceptions to failures."""
        if "__parse_error__" in arguments:
            return ToolResult.failure(
                f"your tool arguments were not valid JSON ({arguments['__parse_error__']}). "
                "Emit a single well-formed JSON object."
            )
        problem = self.validate(arguments)
        if problem:
            return ToolResult.failure(problem)
        try:
            return self.run(**arguments)
        except TypeError as exc:
            return ToolResult.failure(f"bad arguments: {exc}")
        except Exception as exc:  # noqa: BLE001 - tools must never crash the loop
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")

    # -- filesystem helpers ------------------------------------------------

    def resolve_path(self, raw: Optional[str], default: Optional[Path] = None) -> Path:
        """Resolve a model-supplied path.

        Relative paths resolve inside the workspace so the agent has a stable,
        predictable scratch area; absolute paths are honoured as given.
        """
        if not raw:
            if default is None:
                raise ValueError("a path is required")
            return default
        path = Path(str(raw)).expanduser()
        if not path.is_absolute():
            path = self.config.workspace / path
        return path

    @property
    def output_limit(self) -> int:
        return int(self.config.get("tools", "max_output_chars", 20000))


__all__ = ["Tool", "ToolResult"]
