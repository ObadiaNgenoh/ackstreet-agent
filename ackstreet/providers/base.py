"""Provider abstraction: messages, responses, and the base client.

A *provider* turns a conversation plus a list of tool schemas into either text
or a set of tool calls. Everything above this layer is provider-agnostic.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import httpx

from ..errors import ProviderError

# A callback receiving incremental text as it is generated.
StreamCallback = Callable[[str], None]


# --------------------------------------------------------------------------
# SSL / proxy helpers
# --------------------------------------------------------------------------

def ssl_verify() -> Any:
    """Return an httpx ``verify`` value.

    Corporate networks and sandboxed environments often export a custom CA
    bundle through one of the standard variables. Honour it when it points at
    a real file; otherwise fall back to httpx's bundled certifi store.
    """
    for var in (
        "ACKSTREET_CA_BUNDLE",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    ):
        candidate = os.environ.get(var)
        if candidate and os.path.exists(candidate):
            return candidate
    return True


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------

@dataclass
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
            "raw_arguments": self.raw_arguments,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments") or {},
            raw_arguments=data.get("raw_arguments", ""),
        )

    def argument(self, key: str, default: Any = None) -> Any:
        """Fetch one argument, tolerating malformed model output."""
        if "__parse_error__" in self.arguments:
            return default
        return self.arguments.get(key, default)


@dataclass
class Message:
    """A single turn in the conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    timestamp: Optional[str] = None

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", "") or "",
            tool_calls=[ToolCall.from_dict(c) for c in data.get("tool_calls", [])],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            timestamp=data.get("timestamp"),
        )

    # -- conversion --------------------------------------------------------

    def to_openai(self) -> Dict[str, Any]:
        """OpenAI chat-completions wire format (also used by Ollama)."""
        payload: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments or json.dumps(call.arguments),
                    },
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name and self.role == "tool":
            payload["name"] = self.name
        return payload

    def to_text(self) -> str:
        if self.role == "tool":
            return f"[tool:{self.name or '?'}] {self.content}"
        if self.tool_calls:
            calls = ", ".join(c.name for c in self.tool_calls)
            return f"{self.content} (tool calls: {calls})".strip()
        return self.content


@dataclass
class ProviderResponse:
    """What a provider returns for one turn."""

    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def to_message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=self.tool_calls)


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    """Parse model-emitted tool arguments, never raising.

    Models occasionally emit truncated or non-object JSON. Rather than crash
    the loop, we return a dict carrying the parse error so the agent can feed
    the mistake back to the model and let it retry.
    """
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"__raw__": text, "__parse_error__": f"invalid JSON ({exc.msg})"}
    if isinstance(parsed, dict):
        return parsed
    return {"__raw__": text, "__parse_error__": "arguments must be a JSON object"}


# --------------------------------------------------------------------------
# Base provider
# --------------------------------------------------------------------------

class BaseProvider(ABC):
    """Common behaviour for every LLM backend."""

    supports_streaming: bool = False

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = timeout
        self.extra = extra or {}
        self.extra_headers: Dict[str, str] = dict(self.extra.get("headers") or {})
        self._client: Optional[httpx.Client] = None

    # -- http --------------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                verify=ssl_verify(),
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "BaseProvider":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- helpers -----------------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = ""
        try:
            body = response.text[:800]
        except Exception:  # pragma: no cover - defensive
            body = "<unreadable body>"
        hint = ""
        if response.status_code in (401, 403):
            hint = (
                " — check that the API key environment variable is set and valid "
                f"(provider '{self.name}')."
            )
        elif response.status_code == 404:
            hint = " — check base_url and model name."
        raise ProviderError(
            f"{self.name}: HTTP {response.status_code} from {response.request.url}{hint}",
            status_code=response.status_code,
            body=body,
        )

    def _post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        try:
            response = self.client.post(url, json=payload, headers=headers)
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"{self.name}: cannot connect to {url}. "
                "Is the server running and is base_url correct?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name}: request to {url} timed out after {self.timeout}s") from exc
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{self.name}: response from {url} was not valid JSON",
                status_code=response.status_code,
                body=response.text[:400],
            ) from exc

    # -- interface ---------------------------------------------------------

    @abstractmethod
    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        stream_callback: Optional[StreamCallback] = None,
    ) -> ProviderResponse:
        """Run one completion turn."""

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """Return ``(ok, message)`` describing whether the backend is usable."""

    def describe(self) -> str:
        return (
            f"{self.name} (type={type(self).__name__.replace('Provider', '').lower()}, "
            f"model={self.model}, base_url={self.base_url or 'n/a'})"
        )


__all__ = [
    "BaseProvider",
    "Message",
    "ProviderResponse",
    "StreamCallback",
    "ToolCall",
    "parse_tool_arguments",
    "ssl_verify",
]
