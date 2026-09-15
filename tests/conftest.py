"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make the package importable when running pytest from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ackstreet.config import Config  # noqa: E402
from ackstreet.providers.base import (  # noqa: E402
    BaseProvider,
    Message,
    ProviderResponse,
    StreamCallback,
    ToolCall,
)


@pytest.fixture()
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """An isolated config whose home directory lives in a temp folder."""
    home = tmp_path / "ackstreet-home"
    monkeypatch.setenv("ACKSTREET_HOME", str(home))
    for key in list(os.environ):
        if key.startswith("ACKSTREET_") and key not in {"ACKSTREET_HOME"}:
            monkeypatch.delenv(key, raising=False)
    cfg = Config.load()
    cfg.ensure_dirs()
    return cfg


class ScriptedProvider(BaseProvider):
    """A provider that replays a fixed list of responses.

    Used to drive the agent loop deterministically in tests without any network
    access or API key.
    """

    supports_streaming = False

    def __init__(self, script: Sequence[ProviderResponse], name: str = "scripted") -> None:
        super().__init__(name=name, base_url="http://mock", model="scripted-model")
        self.script: List[ProviderResponse] = list(script)
        self.calls: List[List[Message]] = []
        self.received_tools: List[List[Dict[str, Any]]] = []

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        stream_callback: StreamCallback | None = None,
    ) -> ProviderResponse:
        self.calls.append(list(messages))
        self.received_tools.append(list(tools or []))
        if not self.script:
            return ProviderResponse(text="(script exhausted)", finish_reason="stop")
        response = self.script.pop(0)
        if stream_callback is not None and response.text:
            stream_callback(response.text)
        return response

    def health_check(self) -> tuple[bool, str]:
        return True, "scripted provider always healthy"


def tool_call(name: str, arguments: Dict[str, Any], call_id: str = "call_1") -> ProviderResponse:
    """Convenience: a response that requests exactly one tool."""
    import json

    return ProviderResponse(
        text="",
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                raw_arguments=json.dumps(arguments),
            )
        ],
        finish_reason="tool_calls",
    )


def final(text: str) -> ProviderResponse:
    return ProviderResponse(text=text, finish_reason="stop")


@pytest.fixture()
def scripted_provider() -> type[ScriptedProvider]:
    return ScriptedProvider


@pytest.fixture()
def make_tool_call():
    return tool_call


@pytest.fixture()
def make_final():
    return final
