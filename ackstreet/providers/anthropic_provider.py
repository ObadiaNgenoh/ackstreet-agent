"""Anthropic Messages API provider.

Anthropic's wire format differs from OpenAI's in three ways that matter here:

* the system prompt is a top-level ``system`` field, not a message;
* tool calls arrive as ``tool_use`` content blocks, and results must be
  returned as ``tool_result`` blocks inside a *user* message;
* there is no ``tool_call_id`` on the assistant message — ids live on the
  individual blocks.
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

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseProvider):
    """Client for ``POST /v1/messages``."""

    supports_streaming = False  # this adapter issues one non-streaming request
    requires_api_key = True
    default_key_env = "ANTHROPIC_API_KEY"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": self.api_key,
        }
        headers.update(self.extra_headers)
        return headers

    # -- message translation ----------------------------------------------

    @staticmethod
    def _translate(
        messages: Sequence[Message],
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Split our messages into ``(system_text, anthropic_messages)``."""
        system_parts: List[str] = []
        out: List[Dict[str, Any]] = []

        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
                continue

            if message.role == "tool":
                # Tool results are user-role blocks referencing the tool_use id.
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or "",
                                "content": message.content,
                            }
                        ],
                    }
                )
                continue

            if message.role == "assistant":
                blocks: List[Dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments if "__parse_error__" not in call.arguments else {},
                        }
                    )
                if not blocks:
                    blocks = [{"type": "text", "text": ""}]
                out.append({"role": "assistant", "content": blocks})
                continue

            out.append({"role": "user", "content": message.content})

        return "\n\n".join(p for p in system_parts if p), out

    @staticmethod
    def _translate_tools(tools: Sequence[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
        """Convert OpenAI-style tool schemas to Anthropic tool schemas."""
        if not tools:
            return []
        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                function = tool["function"]
                converted.append(
                    {
                        "name": function.get("name", ""),
                        "description": function.get("description", ""),
                        "input_schema": function.get("parameters")
                        or {"type": "object", "properties": {}},
                    }
                )
            elif "name" in tool:
                converted.append(tool)
        return converted

    # -- interface ---------------------------------------------------------

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[Dict[str, Any]] | None = None,
        temperature: float = 0.2,
        stream_callback: Optional[StreamCallback] = None,
    ) -> ProviderResponse:
        system_text, anthropic_messages = self._translate(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(self.extra.get("max_tokens", 4096)),
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if system_text:
            payload["system"] = system_text

        converted_tools = self._translate_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools

        url = f"{self.base_url}/v1/messages" if not self.base_url.endswith("/v1") else f"{self.base_url}/messages"
        self.check_credentials()
        data = self._post(url, payload, self._headers())

        if not isinstance(data, dict):
            raise ProviderError(
                f"{self.name}: expected a JSON object from {url}, got {type(data).__name__}",
                body=str(data)[:400],
            )

        # Anthropic reports failures in the body for some error classes.
        error = data.get("error")
        if error:
            if isinstance(error, dict):
                error = error.get("message") or json.dumps(error)
            raise ProviderError(f"{self.name}: the API returned an error: {str(error)[:400]}")

        content = data.get("content")
        if content is None:
            raise ProviderError(
                f"{self.name}: response from {url} had no 'content' field",
                body=json.dumps(data)[:400],
            )
        if not isinstance(content, list):
            raise ProviderError(
                f"{self.name}: 'content' was {type(content).__name__}, expected a list",
                body=json.dumps(data)[:400],
            )

        text_parts: List[str] = []
        calls: List[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "tool_use":
                name = block.get("name") or ""
                if not name:
                    # An unnamed tool_use block cannot be dispatched.
                    continue
                calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=name,
                        arguments=parse_tool_arguments(block.get("input")),
                        raw_arguments=json.dumps(block.get("input") or {}),
                    )
                )

        text = "".join(text_parts)
        stop_reason = data.get("stop_reason", "") or ""

        # A response that stopped for a tool but supplied no usable call would
        # otherwise be read as a finished, empty answer.
        if stop_reason == "tool_use" and not calls:
            raise ProviderError(
                f"{self.name}: stop_reason was 'tool_use' but no usable tool_use "
                "block was present",
                body=json.dumps(data)[:400],
            )

        if stream_callback is not None and text:
            # Anthropic streaming is not implemented in this adapter; emit once
            # so callers still see progress.
            stream_callback(text)

        return ProviderResponse(
            text=text,
            tool_calls=calls,
            finish_reason=stop_reason,
            usage=data.get("usage") or {},
            raw=data,
        )

    def health_check(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "ANTHROPIC_API_KEY is not set"
        if not self.model:
            return False, "model is not set"
        url = f"{self.base_url}/v1/models" if not self.base_url.endswith("/v1") else f"{self.base_url}/models"
        try:
            response = self.client.get(url, headers=self._headers(), timeout=15.0)
        except httpx.ConnectError:
            return False, f"cannot reach {url}"
        except httpx.TimeoutException:
            return False, f"timed out reaching {url}"
        if response.status_code == 401:
            return False, "HTTP 401 — ANTHROPIC_API_KEY is invalid"
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code} from {url}"
        return True, f"reachable at {url} (model={self.model})"


__all__ = ["AnthropicProvider"]
