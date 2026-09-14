"""OpenAI-compatible provider.

Works against anything that speaks the ``/chat/completions`` schema: OpenAI,
Azure OpenAI, LiteLLM, vLLM, LM Studio, llama.cpp server, OpenRouter, Groq,
Together, DeepSeek, and more. This is the most portable backend available.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

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
            if key not in {"headers", "model"}:
                payload.setdefault(key, value)
        return payload

    # -- non-streaming -----------------------------------------------------

    def _chat_once(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None,
        temperature: float,
    ) -> ProviderResponse:
        url = f"{self.base_url}/chat/completions"
        data = self._post(url, self._build_payload(messages, tools, temperature, False), self._headers())

        if "choices" not in data or not data["choices"]:
            raise ProviderError(
                f"{self.name}: response contained no choices",
                body=json.dumps(data)[:400],
            )

        choice = data["choices"][0]
        message = choice.get("message") or {}

        calls: List[ToolCall] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            raw_args = function.get("arguments")
            calls.append(
                ToolCall(
                    id=raw_call.get("id") or f"call_{index}",
                    name=function.get("name", ""),
                    arguments=parse_tool_arguments(raw_args),
                    raw_arguments=raw_args if isinstance(raw_args, str) else json.dumps(raw_args or {}),
                )
            )

        return ProviderResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            finish_reason=choice.get("finish_reason", "") or "",
            usage=data.get("usage") or {},
            raw=data,
        )

    # -- streaming ---------------------------------------------------------

    def _chat_stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None,
        temperature: float,
        stream_callback: StreamCallback,
    ) -> ProviderResponse:
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(messages, tools, temperature, True)

        text_parts: List[str] = []
        # Tool-call fragments arrive incrementally and are keyed by index.
        partial: Dict[int, Dict[str, Any]] = {}
        finish_reason = ""
        usage: Dict[str, Any] = {}

        try:
            with self.client.stream(
                "POST", url, json=payload, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for_status(response)

                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[len("data:"):].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("usage"):
                        usage = chunk["usage"]

                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            stream_callback(piece)

                        for raw_call in delta.get("tool_calls") or []:
                            idx = raw_call.get("index", 0)
                            slot = partial.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""}
                            )
                            if raw_call.get("id"):
                                slot["id"] = raw_call["id"]
                            function = raw_call.get("function") or {}
                            if function.get("name"):
                                slot["name"] = function["name"]
                            if function.get("arguments"):
                                slot["arguments"] += function["arguments"]
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"{self.name}: cannot connect to {url}. "
                "Is the server running and is base_url correct?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name}: stream to {url} timed out") from exc

        calls = [
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=parse_tool_arguments(slot["arguments"]),
                raw_arguments=slot["arguments"],
            )
            for idx, slot in sorted(partial.items())
        ]

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
            return False, f"HTTP 401 from {url} — API key missing or invalid"
        if response.status_code == 404:
            # Many compatible servers omit /models but still chat fine.
            return True, f"{url} returned 404 but endpoint may still serve chat"
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code} from {url}"
        return True, f"reachable at {url} (model={self.model})"


__all__ = ["OpenAICompatibleProvider"]
