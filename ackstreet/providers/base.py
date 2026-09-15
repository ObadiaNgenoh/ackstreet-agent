"""Provider abstraction: messages, responses, and the base client.

A *provider* turns a conversation plus a list of tool schemas into either text
or a set of tool calls. Everything above this layer is provider-agnostic.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional

import httpx

from ..errors import ProviderError

# A callback receiving incremental text as it is generated.
StreamCallback = Callable[[str], None]

#: HTTP statuses worth retrying: transient rate limits, locks and server faults.
RETRYABLE_STATUS: FrozenSet[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


@dataclass
class RetryPolicy:
    """How hard to retry a transient provider failure."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    retry_statuses: FrozenSet[int] = RETRYABLE_STATUS

    @classmethod
    def from_extra(cls, extra: Optional[Dict[str, Any]] = None) -> RetryPolicy:
        """Build from ``providers.<name>`` extras, ignoring junk values."""
        extra = extra or {}

        def as_int(key: str, default: int) -> int:
            try:
                return int(extra.get(key, default))
            except (TypeError, ValueError):
                return default

        def as_float(key: str, default: float) -> float:
            try:
                return float(extra.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            # `max_retries` counts retries *after* the first attempt, which is
            # how people normally read the word.
            max_attempts=max(1, as_int("max_retries", 2) + 1),
            base_delay=max(0.0, as_float("retry_base_delay", 0.5)),
            max_delay=max(0.0, as_float("retry_max_delay", 8.0)),
        )

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff: base, 2x, 4x ... capped at ``max_delay``."""
        attempt = max(1, attempt)
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))


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
    def from_dict(cls, data: Dict[str, Any]) -> ToolCall:
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
    def from_dict(cls, data: Dict[str, Any]) -> Message:
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
    #: Whether a usable API key is required before making requests.
    requires_api_key: bool = True
    #: Environment variable consulted when the config does not name one.
    default_key_env: str = ""

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        extra: Optional[Dict[str, Any]] = None,
        api_key_env: str = "",
    ) -> None:
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = timeout
        self.api_key_env = api_key_env or ""
        self.extra = extra or {}
        self.extra_headers: Dict[str, str] = dict(self.extra.get("headers") or {})
        self.retry = RetryPolicy.from_extra(self.extra)
        # Indirected so tests can make backoff instant instead of sleeping.
        self._sleep: Callable[[float], None] = time.sleep
        self._client: Optional[httpx.Client] = None

    # -- http --------------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            # An explicit Timeout separates "the server never answered"
            # (connect) from "the model is still thinking" (read). The read
            # budget stays generous because a long completion is legitimate.
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)),
                verify=ssl_verify(),
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> BaseProvider:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- credentials -------------------------------------------------------

    @property
    def key_env_var(self) -> str:
        """Name of the environment variable that should hold the API key."""
        return self.api_key_env or self.default_key_env

    def check_credentials(self) -> None:
        """Fail early and actionably when the API key is missing.

        A blank key otherwise surfaces as a puzzling 401 several seconds later;
        catching it here lets us name the variable the user must export.
        """
        if not self.requires_api_key or self.api_key:
            return
        env = self.key_env_var or "the provider's API key variable"
        raise ProviderError(
            f"{self.name}: no API key configured. Export {env} "
            f"(e.g. `export {env}=...`) or set providers.{self.name}.api_key_env "
            "in ~/.ackstreet/config.toml. For a fully local model, switch to the "
            "ollama provider, which needs no key."
        )

    # -- retry -------------------------------------------------------------

    def _backoff(self, attempt: int, reason: str) -> float:
        """Sleep before the next attempt; returns the delay used."""
        _ = reason  # kept for readable call sites and future logging
        delay = self.retry.delay_for(attempt)
        if delay > 0:
            self._sleep(delay)
        return delay

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> httpx.Response:
        """Send a request, retrying transient failures with exponential backoff.

        Retried: HTTP 408/409/425/429/5xx and read timeouts.
        Not retried: connection errors (a wrong ``base_url`` never fixes
        itself) and other 4xx (a bad key or model never fixes itself either).
        """
        attempts = max(1, int(self.retry.max_attempts))
        budget = timeout or self.timeout

        for attempt in range(1, attempts + 1):
            try:
                response = self.client.request(
                    method,
                    url,
                    json=json_body,
                    headers=headers,
                    timeout=budget,
                )
            except httpx.TimeoutException as exc:
                if attempt < attempts:
                    self._backoff(attempt, "timeout")
                    continue
                raise ProviderError(
                    f"{self.name}: request to {url} timed out after {budget}s "
                    f"(gave up after {attempts} attempt(s))"
                ) from exc
            except httpx.ConnectError as exc:
                raise ProviderError(
                    f"{self.name}: cannot connect to {url}. "
                    "Is the server running and is base_url correct?"
                ) from exc

            if response.status_code in self.retry.retry_statuses and attempt < attempts:
                self._backoff(attempt, f"HTTP {response.status_code}")
                continue

            self._raise_for_status(response)
            return response

        # Unreachable: every path above returns or raises.
        raise ProviderError(f"{self.name}: request to {url} failed")

    def _open_stream(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> httpx.Response:
        """Open a streaming response, retrying only before any data is read.

        Retrying mid-stream would duplicate output the caller has already seen,
        so failures after the first byte surface as errors instead.
        """
        attempts = max(1, int(self.retry.max_attempts))

        for attempt in range(1, attempts + 1):
            try:
                request = self.client.build_request(
                    "POST", url, json=payload, headers=headers
                )
                response = self.client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                if attempt < attempts:
                    self._backoff(attempt, "stream timeout")
                    continue
                raise ProviderError(
                    f"{self.name}: streaming request to {url} timed out "
                    f"(gave up after {attempts} attempt(s))"
                ) from exc
            except httpx.ConnectError as exc:
                raise ProviderError(
                    f"{self.name}: cannot connect to {url}. "
                    "Is the server running and is base_url correct?"
                ) from exc

            if response.status_code in self.retry.retry_statuses and attempt < attempts:
                response.close()
                self._backoff(attempt, f"HTTP {response.status_code}")
                continue

            if response.status_code >= 400:
                response.read()
                self._raise_for_status(response)
            return response

        raise ProviderError(f"{self.name}: could not open a stream to {url}")

    # -- helpers -----------------------------------------------------------

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = ""
        try:
            body = response.text[:800]
        except Exception:  # pragma: no cover - defensive
            body = "<unreadable body>"
        try:
            url: Any = response.request.url
        except Exception:  # pragma: no cover - defensive
            url = "<unknown url>"

        status = response.status_code
        retries = max(0, self.retry.max_attempts - 1)

        if status in (401, 403):
            env = self.key_env_var or "the provider's API key variable"
            hint = (
                " — the API key is missing, invalid or not permitted. "
                f"Check that {env} is set to a valid key for this endpoint."
            )
        elif status == 404:
            hint = " — check base_url and model name."
        elif status == 429:
            hint = (
                " — rate limited or out of quota. The request was retried "
                f"{retries} time(s) before giving up; slow down or raise "
                f"providers.{self.name}.max_retries."
            )
        elif 500 <= status < 600:
            hint = (
                f" — the provider had a server error. Retried {retries} "
                "time(s) before giving up."
            )
        elif status == 400:
            hint = (
                " — the request was rejected; usually an unknown model name "
                "or an unsupported parameter."
            )
        else:
            hint = ""
        raise ProviderError(
            f"{self.name}: HTTP {status} from {url}{hint}",
            status_code=status,
            body=body,
        )

    def _post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Any:
        """POST JSON and decode the response, with retries and clear errors."""
        response = self._request("POST", url, json_body=payload, headers=headers)
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
    "RETRYABLE_STATUS",
    "BaseProvider",
    "Message",
    "ProviderResponse",
    "RetryPolicy",
    "StreamCallback",
    "ToolCall",
    "parse_tool_arguments",
    "ssl_verify",
]
