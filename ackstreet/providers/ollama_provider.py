"""Ollama provider for fully local models.

Ollama exposes two surfaces. This adapter prefers the native ``/api/chat``
endpoint (better tool-call support and no key required) and reports clearly
when the daemon is unreachable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
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


class OllamaProvider(BaseProvider):
    """Client for a local Ollama daemon."""

    supports_streaming = True
    # A local daemon needs no credentials; requiring a key here would be wrong.
    requires_api_key = False
    default_key_env = ""

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        stream_callback: Optional[StreamCallback] = None,
    ) -> ProviderResponse:
        url = f"{self.base_url}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = list(tools)

        data = self._post(url, payload, self._headers())

        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name}: expected a JSON object from {url}, got {type(data).__name__}",
                body=str(data)[:400],
            )

        error = data.get("error")
        if error:
            raise ProviderError(
                f"{self.name}: the Ollama daemon returned an error: {str(error)[:400]}"
            )

        message = data.get("message")
        if message is None:
            raise ProviderError(
                f"{self.name}: response from {url} had no 'message' field",
                body=json.dumps(data)[:400],
            )
        if not isinstance(message, dict):
            raise ProviderError(
                f"{self.name}: 'message' was {type(message).__name__}, expected an object",
                body=json.dumps(data)[:400],
            )

        calls: List[ToolCall] = []
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function")
            if not isinstance(function, dict):
                function = {}
            name = function.get("name") or ""
            if not name:
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

        text = message.get("content") or ""
        if stream_callback is not None and text:
            stream_callback(text)

        return ProviderResponse(
            text=text,
            tool_calls=calls,
            finish_reason=data.get("done_reason", "") or ("stop" if data.get("done") else ""),
            usage={
                "prompt_tokens": data.get("prompt_eval_count"),
                "completion_tokens": data.get("eval_count"),
            },
            raw=data,
        )

    def health_check(self) -> tuple[bool, str]:
        url = f"{self.base_url}/api/tags"
        try:
            response = self.client.get(url, headers=self._headers(), timeout=10.0)
        except httpx.ConnectError:
            return False, (
                f"cannot reach {url} — start Ollama with `ollama serve` "
                "(or set providers.ollama.base_url to a remote host)"
            )
        except httpx.TimeoutException:
            return False, f"timed out reaching {url}"

        if response.status_code >= 400:
            return False, f"HTTP {response.status_code} from {url}"

        try:
            models = [m.get("name", "") for m in (response.json().get("models") or [])]
        except ValueError:
            models = []

        if models and not any(m.split(":")[0] == self.model.split(":")[0] for m in models):
            return True, (
                f"daemon reachable, but model '{self.model}' was not found. "
                f"Run `ollama pull {self.model}`. Installed: {', '.join(models) or 'none'}"
            )
        return True, f"reachable at {url} (model={self.model})"


__all__ = ["OllamaProvider"]
