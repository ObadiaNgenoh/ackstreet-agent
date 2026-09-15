"""OpenAI-compatible provider.

Works against anything that speaks the ``/chat/completions`` schema: OpenAI,
Azure OpenAI, LiteLLM, vLLM, LM Studio, llama.cpp server, OpenRouter, Groq,
Together, DeepSeek, and more. This is the most portable backend available.

Robustness notes:

* transient failures (429, 5xx, timeouts) are retried with exponential backoff
  by :meth:`BaseProvider._request`;
* a missing API key is reported before the request is sent;
* a response that is not an object, carries no ``choices``, reports an error in
  the body, or claims tool calls without supplying any is turned into a clear
  :class:`ProviderError` rather than an ``AttributeError`` deep in the loop.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, Dict, List, Optional

import httpx

from ..errors import ProviderError
from .base import (
    BaseProvider,
    Message,
    ProviderResponse,
    StreamCallback,
    ToolCall,
    parse_tool_arguments,
)


class OpenAICompatibleProvider(BaseProvider):
    """Client for any OpenAI-compatible chat-completions endpoint."""

    supports_streaming = True
    requires_api_key = True
    default_key_env = "OPENAI_API_KEY"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None,
        temperature: float,
        stream: bool,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        # Pass through any provider-specific extras (e.g. top_p, seed).
        for key, value in self.extra.items():
            if key not in {"headers", "model", "max_retries", "retry_base_delay", "retry_max_delay"}:
                payload.setdefault(key, value)
        return payload

    # -- response parsing --------------------------------------------------

    def _parse_tool_calls(self, message: Dict[str, Any]) -> List[ToolCall]:
        """Turn the wire format's ``tool_calls`` into :class:`ToolCall` objects.

        Malformed entries are skipped rather than raising: a nameless call
        cannot be dispatched anyway, and one bad entry should not discard the
        other valid ones. An *unparseable argument string* is preserved as a
        ``__parse_error__`` marker so the agent can correct it and retry.
        """
        raw_calls = message.get("tool_calls")
        if raw_calls is None:
            return []
        if not isinstance(raw_calls, list):
            raise ProviderError(
                f"{self.name}: 'tool_calls' was {type(raw_calls).__name__}, expected a list"
            )

        calls: List[ToolCall] = []
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function")
            if not isinstance(function, dict):
                function = {}
            name = function.get("name") or ""
            if not name:
                # Cannot dispatch a call with no tool name.
                continue

            raw_args = function.get("arguments")
            if raw_args is None or (isinstance(raw_args, str) and not raw_args.strip()):
                raw_args = "{}"

            calls.append(
                ToolCall(
                    id=raw_call.get("id") or f"call_{index}",
                    name=name,
                    arguments=parse_tool_arguments(raw_args),
                    raw_arguments=(
                        raw_args if isinstance(raw_args, str) else json.dumps(raw_args or {})
                    ),
                )
            )
        return calls

    def _parse_completion(self, data: Any) -> ProviderResponse:
        """Validate and decode a non-streaming completion body."""
        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name}: expected a JSON object from the endpoint, got "
                f"{type(data).__name__}",
                body=str(data)[:400],
            )

        # Several compatible servers report failures in the body with HTTP 200.
        error = data.get("error")
        if error:
            if isinstance(error, dict):
                error = error.get("message") or json.dumps(error)
            raise ProviderError(f"{self.name}: the endpoint returned an error: {str(error)[:400]}")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                f"{self.name}: response contained no choices",
                body=json.dumps(data)[:400],
            )

        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message")
        if not isinstance(message, dict):
            # Some servers emit {"delta": ...} instead of {"message": ...}.
            delta = choice.get("delta")
            message = delta if isinstance(delta, dict) else {}

        calls = self._parse_tool_calls(message)
        finish_reason = choice.get("finish_reason") or ""

        # A truncated response can announce tool calls and then supply none.
        # Ending the run silently would look like a successful answer.
        if finish_reason == "tool_calls" and not calls:
            raise ProviderError(
                f"{self.name}: finish_reason was 'tool_calls' but the response "
                "contained no usable tool call",
                body=json.dumps(data)[:400],
            )

        return ProviderResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            finish_reason=finish_reason,
            usage=data.get("usage") or {},
            raw=data,
        )

    # -- non-streaming -----------------------------------------------------

    def _chat_once(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None,
        temperature: float,
    ) -> ProviderResponse:
        self.check_credentials()
        url = f"{self.base_url}/chat/completions"
        data = self._post(
            url, self._build_payload(messages, tools, temperature, False), self._headers()
        )
        return self._parse_completion(data)

    # -- streaming ---------------------------------------------------------

    @staticmethod
    def _iter_sse_payloads(lines: Iterable[str]) -> Iterable[Dict[str, Any]]:
        """Decode SSE ``data:`` lines, ignoring keep-alives and bad JSON."""
        for line in lines:
            if not line:
                continue
            if line.startswith("data:"):
                line = line[len("data:"):].strip()
            if not line or line == "[DONE]":
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                # A malformed keep-alive must not abort a working stream.
                continue
            if isinstance(chunk, dict):
                yield chunk

    def _chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None,
        temperature: float,
        stream_callback: StreamCallback,
    ) -> ProviderResponse:
        self.check_credentials()
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(messages, tools, temperature, True)

        text_parts: List[str] = []
        # Tool-call fragments arrive incrementally and are keyed by index.
        partial: Dict[int, Dict[str, Any]] = {}
        finish_reason = ""
        usage: Dict[str, Any] = {}
        saw_event = False

        response = self._open_stream(url, payload, self._headers())
        try:
            for chunk in self._iter_sse_payloads(response.iter_lines()):
                saw_event = True

                if chunk.get("usage"):
                    usage = chunk["usage"]

                error = chunk.get("error")
                if error:
                    if isinstance(error, dict):
                        error = error.get("message") or json.dumps(error)
                    raise ProviderError(
                        f"{self.name}: the stream reported an error: {str(error)[:400]}"
                    )

                for choice in chunk.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        delta = {}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

                    piece = delta.get("content")
                    if piece:
                        text_parts.append(piece)
                        stream_callback(piece)

                    for raw_call in delta.get("tool_calls") or []:
                        if not isinstance(raw_call, dict):
                            continue
                        idx = raw_call.get("index", 0)
                        if not isinstance(idx, int):
                            idx = 0
                        slot = partial.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if raw_call.get("id"):
                            slot["id"] = raw_call["id"]
                        function = raw_call.get("function")
                        if not isinstance(function, dict):
                            continue
                        if function.get("name"):
                            slot["name"] = function["name"]
                        if function.get("arguments"):
                            slot["arguments"] += function["arguments"]
        finally:
            # Closed explicitly rather than via `with`: the response may come
            # from a client that does not implement the context-manager
            # protocol, and the stream must be released either way.
            response.close()

        calls = [
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=parse_tool_arguments(slot["arguments"]),
                raw_arguments=slot["arguments"],
            )
            for idx, slot in sorted(partial.items())
            if slot["name"]
        ]

        if not saw_event and not text_parts and not calls:
            raise ProviderError(
                f"{self.name}: the stream closed without producing any events"
            )

        return ProviderResponse(
            text="".join(text_parts),
            tool_calls=calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    # -- interface ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        stream_callback: Optional[StreamCallback] = None,
    ) -> ProviderResponse:
        if stream_callback is not None:
            return self._chat_stream(messages, tools, temperature, stream_callback)
        return self._chat_once(messages, tools, temperature)

    def health_check(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "base_url is not set"
        if not self.model:
            return False, "model is not set"
        url = f"{self.base_url}/models"
        try:
            response = self.client.get(url, headers=self._headers(), timeout=15.0)
        except httpx.ConnectError:
            return False, f"cannot reach {url}"
        except httpx.TimeoutException:
            return False, f"timed out reaching {url}"
        if response.status_code == 401:
            env = self.key_env_var or "the API key variable"
            return False, f"HTTP 401 from {url} \u2014 {env} is missing or invalid"
        if response.status_code == 404:
            # Many compatible servers omit /models but still chat fine.
            return True, f"{url} returned 404 but endpoint may still serve chat"
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code} from {url}"
        return True, f"reachable at {url} (model={self.model})"


__all__ = ["OpenAICompatibleProvider"]
