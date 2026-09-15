"""Provider-layer hardening tests.

Every network interaction is faked at the ``httpx`` boundary — a
:class:`FakeClient` stands in for :class:`httpx.Client` and returns real
:class:`httpx.Response` objects, so response parsing is exercised for real
without a socket, an API key or a live server.

Covered per provider:

* retry with exponential backoff on 429 / 5xx / read timeouts, then success;
* giving up after the retry budget, with an error that says so;
* *not* retrying a connect error or a 401 (those never fix themselves);
* a clear message naming the missing environment variable when no key is set;
* malformed / empty / inconsistent tool-call responses raising
  :class:`ProviderError` instead of crashing the loop;
* the streaming path producing incremental text and assembling fragmented
  tool calls, including through the agent's own ``chat`` turn.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Dict, List, Optional

import httpx
import pytest

from ackstreet.agent import Agent
from ackstreet.config import Config
from ackstreet.errors import ProviderError
from ackstreet.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from ackstreet.providers.base import RetryPolicy

# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeClient:
    """Minimal stand-in for :class:`httpx.Client`.

    ``script`` is consumed one entry per HTTP call. An entry is either an
    :class:`httpx.Response` to return or an exception instance to raise.
    """

    def __init__(self, script: Sequence[Any]) -> None:
        self.script: List[Any] = list(script)
        self.calls: List[Dict[str, Any]] = []
        self.closed = False

    def _next(self, url: str = "") -> Any:
        if not self.script:
            raise AssertionError("FakeClient script exhausted — unexpected extra HTTP call")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if item.request is None and url:
            item.request = httpx.Request("POST", url)
        return item

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._next(url)

    def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
        return httpx.Request(method, url, **kwargs)

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": request.method, "url": str(request.url), "stream": True})
        response = self._next(str(request.url))
        if response.request is None:
            response.request = request
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next(url)

    def close(self) -> None:
        self.closed = True


def http_response(
    status: int = 200,
    payload: Any = None,
    content: Optional[bytes] = None,
) -> httpx.Response:
    """Build a real httpx response with a request attached."""
    request = httpx.Request("POST", "http://test.local/v1/chat/completions")
    if content is not None:
        return httpx.Response(status, content=content, request=request)
    if payload is not None:
        return httpx.Response(status, json=payload, request=request)
    return httpx.Response(status, request=request)


def completion(text: str = "hi", tool_calls: Optional[List[Dict[str, Any]]] = None,
               finish_reason: str = "stop") -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "x",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }


def sse(*chunks: Dict[str, Any]) -> bytes:
    """Encode dicts as an SSE byte stream, terminated with [DONE]."""
    body = "".join(f"data: {json_dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode("utf-8")


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj)


def text_chunk(piece: str) -> Dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}


def make_openai(script: Sequence[Any], **extra: Any) -> tuple[OpenAICompatibleProvider, FakeClient]:
    """An OpenAI-compatible provider wired to a fake client with instant backoff."""
    merged = {"max_retries": 2, "retry_base_delay": 0, "retry_max_delay": 0}
    merged.update(extra)
    provider = OpenAICompatibleProvider(
        name="openai",
        base_url="http://test.local/v1",
        model="test-model",
        api_key="test-key",
        timeout=5.0,
        extra=merged,
        api_key_env="OPENAI_API_KEY",
    )
    client = FakeClient(script)
    provider._client = client
    provider._sleep = lambda _seconds: None  # no real waiting in tests
    return provider, client


def make_anthropic(script: Sequence[Any], api_key: str = "test-key") -> tuple[AnthropicProvider, FakeClient]:
    provider = AnthropicProvider(
        name="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-test",
        api_key=api_key,
        timeout=5.0,
        extra={"max_retries": 2, "retry_base_delay": 0, "retry_max_delay": 0},
        api_key_env="ANTHROPIC_API_KEY",
    )
    client = FakeClient(script)
    provider._client = client
    provider._sleep = lambda _seconds: None
    return provider, client


def make_ollama(script: Sequence[Any]) -> tuple[OllamaProvider, FakeClient]:
    provider = OllamaProvider(
        name="ollama",
        base_url="http://localhost:11434",
        model="llama-test",
        api_key="",
        timeout=5.0,
        extra={"max_retries": 2, "retry_base_delay": 0, "retry_max_delay": 0},
    )
    client = FakeClient(script)
    provider._client = client
    provider._sleep = lambda _seconds: None
    return provider, client


# --------------------------------------------------------------------------
# Retry policy arithmetic
# --------------------------------------------------------------------------

def test_retry_policy_delays_grow_exponentially_and_cap() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=0.5, max_delay=8.0)
    assert policy.delay_for(1) == 0.5
    assert policy.delay_for(2) == 1.0
    assert policy.delay_for(3) == 2.0
    assert policy.delay_for(4) == 4.0
    assert policy.delay_for(5) == 8.0
    # Never exceeds the ceiling.
    assert policy.delay_for(9) == 8.0


def test_retry_policy_reads_max_retries_as_extra_attempts() -> None:
    assert RetryPolicy.from_extra({"max_retries": 0}).max_attempts == 1
    assert RetryPolicy.from_extra({"max_retries": 2}).max_attempts == 3
    # Defaults are used when the config is absent or nonsense.
    assert RetryPolicy.from_extra(None).max_attempts == 3
    assert RetryPolicy.from_extra({"max_retries": "nonsense"}).max_attempts == 3


# --------------------------------------------------------------------------
# Retry behaviour: 429 / 5xx / timeout
# --------------------------------------------------------------------------

def test_retries_429_then_succeeds() -> None:
    provider, client = make_openai([http_response(429), http_response(429),
                                    http_response(200, completion("recovered"))])
    result = provider.chat(messages=[])
    assert result.text == "recovered"
    assert len(client.calls) == 3, "should have retried twice"


def test_retries_500_then_succeeds() -> None:
    provider, client = make_openai([http_response(503), http_response(200, completion("ok"))])
    assert provider.chat(messages=[]).text == "ok"
    assert len(client.calls) == 2


def test_retries_read_timeout_then_succeeds() -> None:
    provider, client = make_openai(
        [httpx.ReadTimeout("read timed out"), http_response(200, completion("after timeout"))]
    )
    assert provider.chat(messages=[]).text == "after timeout"
    assert len(client.calls) == 2


def test_gives_up_after_retry_budget_on_429() -> None:
    provider, client = make_openai([http_response(429), http_response(429), http_response(429)])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    message = str(excinfo.value)
    assert "429" in message
    assert "rate limited" in message
    assert "retried 2 time(s)" in message
    assert len(client.calls) == 3, "must not retry past the budget"


def test_gives_up_after_retry_budget_on_timeout() -> None:
    provider, client = make_openai(
        [httpx.ReadTimeout("t"), httpx.ReadTimeout("t"), httpx.ReadTimeout("t")]
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "timed out" in str(excinfo.value)
    assert "gave up after 3 attempt(s)" in str(excinfo.value)
    assert len(client.calls) == 3


def test_connect_error_is_not_retried() -> None:
    """A wrong base_url will never fix itself, so retrying just wastes time."""
    provider, client = make_openai(
        [httpx.ConnectError("refused"), http_response(200, completion("should not reach"))]
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "cannot connect" in str(excinfo.value)
    assert len(client.calls) == 1


def test_401_is_not_retried_and_names_the_key_variable() -> None:
    provider, client = make_openai([http_response(401)])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    message = str(excinfo.value)
    assert "401" in message
    assert "OPENAI_API_KEY" in message
    assert len(client.calls) == 1


def test_400_mentions_model_or_parameter() -> None:
    provider, _ = make_openai([http_response(400)])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "unknown model name" in str(excinfo.value)


def test_every_request_carries_an_explicit_timeout() -> None:
    provider, client = make_openai([http_response(200, completion("ok"))])
    provider.chat(messages=[])
    assert client.calls[0]["timeout"] == 5.0


def test_client_is_built_with_a_structured_timeout() -> None:
    provider = OpenAICompatibleProvider(
        name="openai", base_url="http://x/v1", model="m", api_key="k", timeout=42.0
    )
    timeout = provider.client.timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0  # capped short so a dead host fails fast
    assert timeout.read == 42.0


# --------------------------------------------------------------------------
# Missing / invalid credentials
# --------------------------------------------------------------------------

def test_missing_key_raises_before_any_http_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        name="openai",
        base_url="http://test.local/v1",
        model="m",
        api_key="",
        api_key_env="OPENAI_API_KEY",
    )
    client = FakeClient([])
    provider._client = client

    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    message = str(excinfo.value)
    assert "no API key configured" in message
    assert "export OPENAI_API_KEY" in message
    assert client.calls == [], "must fail before hitting the network"


def test_anthropic_missing_key_names_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider, client = make_anthropic([], api_key="")
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert client.calls == []


def test_anthropic_default_key_env_is_used_when_config_is_silent() -> None:
    provider = AnthropicProvider(
        name="anthropic", base_url="https://api.anthropic.com", model="m", api_key=""
    )
    assert provider.key_env_var == "ANTHROPIC_API_KEY"


def test_ollama_requires_no_key() -> None:
    provider, client = make_ollama(
        [http_response(200, {"message": {"role": "assistant", "content": "local"}, "done": True})]
    )
    provider.check_credentials()  # must not raise
    assert provider.chat(messages=[]).text == "local"
    assert len(client.calls) == 1


# --------------------------------------------------------------------------
# Malformed / empty responses
# --------------------------------------------------------------------------

def test_empty_choices_raises_a_clear_error() -> None:
    provider, _ = make_openai([http_response(200, {"choices": []})])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "no choices" in str(excinfo.value)


def test_non_object_body_raises() -> None:
    provider, _ = make_openai([http_response(200, [1, 2, 3])])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "expected a JSON object" in str(excinfo.value)


def test_body_level_error_with_http_200_raises() -> None:
    provider, _ = make_openai(
        [http_response(200, {"error": {"message": "model not found", "type": "invalid_request"}})]
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "model not found" in str(excinfo.value)


def test_invalid_json_body_raises() -> None:
    provider, _ = make_openai([http_response(200, content=b"<html>not json</html>")])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "not valid JSON" in str(excinfo.value)


def test_tool_calls_not_a_list_raises() -> None:
    payload = completion()
    payload["choices"][0]["message"]["tool_calls"] = {"name": "shell"}
    provider, _ = make_openai([http_response(200, payload)])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "expected a list" in str(excinfo.value)


def test_finish_reason_tool_calls_without_calls_raises() -> None:
    """A truncated turn must not be mistaken for a finished answer."""
    provider, _ = make_openai([http_response(200, completion("", finish_reason="tool_calls"))])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "no usable tool call" in str(excinfo.value)


def test_nameless_tool_call_is_skipped() -> None:
    payload = completion(
        "",
        tool_calls=[
            {"id": "c0", "type": "function", "function": {"name": "", "arguments": "{}"}},
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "shell", "arguments": '{"command": "ls"}'},
            },
        ],
        finish_reason="tool_calls",
    )
    provider, _ = make_openai([http_response(200, payload)])
    result = provider.chat(messages=[])
    assert [c.name for c in result.tool_calls] == ["shell"]
    assert result.tool_calls[0].arguments == {"command": "ls"}


def test_malformed_tool_arguments_become_a_parse_error_not_a_crash() -> None:
    payload = completion(
        "",
        tool_calls=[
            {
                "id": "c0",
                "type": "function",
                "function": {"name": "shell", "arguments": '{"command": "ls"'},
            }
        ],
        finish_reason="tool_calls",
    )
    provider, _ = make_openai([http_response(200, payload)])
    result = provider.chat(messages=[])
    assert len(result.tool_calls) == 1
    assert "__parse_error__" in result.tool_calls[0].arguments
    # The agent-facing accessor yields the default instead of exploding.
    assert result.tool_calls[0].argument("command", "fallback") == "fallback"


def test_missing_arguments_becomes_an_empty_object() -> None:
    payload = completion(
        "",
        tool_calls=[{"id": "c0", "type": "function", "function": {"name": "list_skills"}}],
        finish_reason="tool_calls",
    )
    provider, _ = make_openai([http_response(200, payload)])
    result = provider.chat(messages=[])
    assert result.tool_calls[0].arguments == {}


def test_response_with_delta_instead_of_message_is_tolerated() -> None:
    """Some compatible servers only send the `delta` shape."""
    payload = {
        "choices": [{"index": 0, "delta": {"content": "from delta"}, "finish_reason": "stop"}]
    }
    provider, _ = make_openai([http_response(200, payload)])
    assert provider.chat(messages=[]).text == "from delta"


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------

def test_stream_yields_incremental_text() -> None:
    provider, _ = make_openai(
        [http_response(200, content=sse(text_chunk("Hello"), text_chunk(", "), text_chunk("world")))]
    )
    chunks: List[str] = []
    result = provider.chat(messages=[], stream_callback=chunks.append)

    assert chunks == ["Hello", ", ", "world"], "callback must fire per token"
    assert result.text == "Hello, world"


def test_stream_assembles_fragmented_tool_calls() -> None:
    fragments = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "shell", "arguments": '{"comm'}}]},
            "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'and": "ls"}'}}]},
            "finish_reason": "tool_calls"}]},
    ]
    provider, _ = make_openai([http_response(200, content=sse(*fragments))])
    result = provider.chat(messages=[], stream_callback=lambda _c: None)

    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.name == "shell"
    assert call.arguments == {"command": "ls"}


def test_stream_ignores_malformed_keepalives() -> None:
    body = b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n' \
           b'data: not-json\n\n' \
           b': keep-alive comment\n\n' \
           b'data: [DONE]\n\n'
    provider, _ = make_openai([http_response(200, content=body)])
    chunks: List[str] = []
    result = provider.chat(messages=[], stream_callback=chunks.append)
    assert chunks == ["ok"]
    assert result.text == "ok"


def test_stream_retries_before_the_first_byte() -> None:
    provider, client = make_openai(
        [
            http_response(503),
            http_response(200, content=sse(text_chunk("recovered stream"))),
        ]
    )
    chunks: List[str] = []
    provider.chat(messages=[], stream_callback=chunks.append)

    assert chunks == ["recovered stream"]
    assert len(client.calls) == 2


def test_stream_reports_a_body_error() -> None:
    body = sse({"error": {"message": "quota exceeded"}})
    provider, _ = make_openai([http_response(200, content=body)])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[], stream_callback=lambda _c: None)
    assert "quota exceeded" in str(excinfo.value)


def test_empty_stream_raises() -> None:
    provider, _ = make_openai([http_response(200, content=b"data: [DONE]\n\n")])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[], stream_callback=lambda _c: None)
    assert "without producing any events" in str(excinfo.value)


def test_stream_http_error_is_reported_not_swallowed() -> None:
    """A 5xx on the stream path is retried to the budget, then reported."""
    provider, client = make_openai(
        [http_response(500, content=b"boom"), http_response(500, content=b"boom"),
         http_response(500, content=b"boom")]
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[], stream_callback=lambda _c: None)
    assert "500" in str(excinfo.value)
    assert len(client.calls) == 3, "5xx is transient, so it must be retried"


def test_stream_non_retryable_status_fails_immediately() -> None:
    """A 400 will never fix itself, so the stream must not be retried."""
    provider, client = make_openai([http_response(400, content=b"bad request")])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[], stream_callback=lambda _c: None)
    assert "400" in str(excinfo.value)
    assert len(client.calls) == 1


def test_chat_routes_to_streaming_when_a_callback_is_given() -> None:
    provider, client = make_openai([http_response(200, content=sse(text_chunk("streamed")))])
    provider.chat(messages=[], stream_callback=lambda _c: None)
    # The streaming path uses send(..., stream=True), not request().
    assert client.calls[0].get("stream") is True


def test_agent_chat_turn_streams_incremental_text(config: Config) -> None:
    """End-to-end: the agent's streaming turn emits per-chunk 'text' events."""
    provider, _ = make_openai(
        [http_response(200, content=sse(text_chunk("Part one. "), text_chunk("Part two.")))]
    )
    events: List[Dict[str, Any]] = []
    agent = Agent(
        config,
        provider=provider,
        on_event=lambda event: events.append({"type": event.type, **event.data}),
    )
    agent.config.set("agent", "auto_curate", False)

    result = agent.chat_turn("say something", stream=True)

    streamed = [e["chunk"] for e in events if e["type"] == "text"]
    assert streamed == ["Part one. ", "Part two."]
    assert result.text == "Part one. Part two."


# --------------------------------------------------------------------------
# Anthropic hardening
# --------------------------------------------------------------------------

def test_anthropic_missing_content_raises() -> None:
    provider, _ = make_anthropic([http_response(200, {"id": "m", "stop_reason": "end_turn"})])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "no 'content' field" in str(excinfo.value)


def test_anthropic_content_not_a_list_raises() -> None:
    provider, _ = make_anthropic([http_response(200, {"content": "just a string"})])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "expected a list" in str(excinfo.value)


def test_anthropic_stop_reason_tool_use_without_a_block_raises() -> None:
    provider, _ = make_anthropic(
        [http_response(200, {"content": [], "stop_reason": "tool_use"})]
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "no usable tool_use block" in str(excinfo.value)


def test_anthropic_skips_unnamed_tool_use_block() -> None:
    payload = {
        "content": [
            {"type": "text", "text": "working"},
            {"type": "tool_use", "id": "t0", "name": "", "input": {}},
            {"type": "tool_use", "id": "t1", "name": "shell", "input": {"command": "ls"}},
        ],
        "stop_reason": "tool_use",
    }
    provider, _ = make_anthropic([http_response(200, payload)])
    result = provider.chat(messages=[])
    assert result.text == "working"
    assert [c.name for c in result.tool_calls] == ["shell"]


def test_anthropic_parses_a_normal_reply() -> None:
    payload = {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}
    provider, _ = make_anthropic([http_response(200, payload)])
    result = provider.chat(messages=[])
    assert result.text == "hello"
    assert result.tool_calls == []


def test_anthropic_retries_429() -> None:
    payload = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}
    provider, client = make_anthropic([http_response(429), http_response(200, payload)])
    assert provider.chat(messages=[]).text == "ok"
    assert len(client.calls) == 2


def test_anthropic_invalid_json_raises() -> None:
    provider, _ = make_anthropic([http_response(200, content=b"nope")])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "not valid JSON" in str(excinfo.value)


# --------------------------------------------------------------------------
# Ollama hardening
# --------------------------------------------------------------------------

def test_ollama_missing_message_raises() -> None:
    provider, _ = make_ollama([http_response(200, {"done": True})])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "no 'message' field" in str(excinfo.value)


def test_ollama_message_not_an_object_raises() -> None:
    provider, _ = make_ollama([http_response(200, {"message": "text"})])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "expected an object" in str(excinfo.value)


def test_ollama_daemon_error_is_surfaced() -> None:
    provider, _ = make_ollama([http_response(200, {"error": "model not found"})])
    with pytest.raises(ProviderError) as excinfo:
        provider.chat(messages=[])
    assert "model not found" in str(excinfo.value)


def test_ollama_parses_tool_calls_and_skips_nameless() -> None:
    payload = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "", "arguments": "{}"}},
                {"function": {"name": "shell", "arguments": '{"command": "pwd"}'}},
            ],
        },
        "done": True,
        "done_reason": "stop",
    }
    provider, _ = make_ollama([http_response(200, payload)])
    result = provider.chat(messages=[])
    assert [c.name for c in result.tool_calls] == ["shell"]
    assert result.tool_calls[0].arguments == {"command": "pwd"}


def test_ollama_retries_503() -> None:
    payload = {"message": {"content": "recovered"}, "done": True}
    provider, client = make_ollama([http_response(503), http_response(200, payload)])
    assert provider.chat(messages=[]).text == "recovered"
    assert len(client.calls) == 2


def test_ollama_stream_callback_receives_the_text() -> None:
    payload = {"message": {"content": "local reply"}, "done": True}
    provider, _ = make_ollama([http_response(200, payload)])
    chunks: List[str] = []
    result = provider.chat(messages=[], stream_callback=chunks.append)
    assert chunks == ["local reply"]
    assert result.text == "local reply"
