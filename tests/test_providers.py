"""Tests for the provider layer — wire formats, parsing and the registry."""

from __future__ import annotations

import json

import pytest

from ackstreet.config import Config
from ackstreet.errors import ConfigError
from ackstreet.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    build_provider,
    provider_from_config,
)
from ackstreet.providers.base import (
    Message,
    ProviderResponse,
    ToolCall,
    parse_tool_arguments,
)

# -- argument parsing ------------------------------------------------------

def test_parse_tool_arguments_valid_json() -> None:
    assert parse_tool_arguments('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_tool_arguments_already_a_dict() -> None:
    assert parse_tool_arguments({"a": 1}) == {"a": 1}


def test_parse_tool_arguments_empty() -> None:
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments(None) == {}


def test_parse_tool_arguments_truncated_json_is_recovered() -> None:
    result = parse_tool_arguments('{"command": "ls -la"')
    assert "__parse_error__" in result
    assert "__raw__" in result


def test_parse_tool_arguments_non_object_json() -> None:
    result = parse_tool_arguments("[1, 2, 3]")
    assert "__parse_error__" in result


# -- message serialisation -------------------------------------------------

def test_message_to_openai_plain() -> None:
    payload = Message(role="user", content="hi").to_openai()
    assert payload == {"role": "user", "content": "hi"}


def test_message_with_tool_calls_to_openai() -> None:
    message = Message(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(id="c1", name="shell", arguments={"command": "ls"}, raw_arguments='{"command":"ls"}')
        ],
    )
    payload = message.to_openai()
    assert payload["tool_calls"][0]["id"] == "c1"
    assert payload["tool_calls"][0]["function"]["name"] == "shell"
    assert json.loads(payload["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}


def test_tool_message_to_openai_includes_call_id() -> None:
    payload = Message(role="tool", content="out", tool_call_id="c9", name="shell").to_openai()
    assert payload["tool_call_id"] == "c9"
    assert payload["name"] == "shell"


def test_message_roundtrip_through_dict() -> None:
    original = Message(
        role="assistant",
        content="text",
        tool_calls=[ToolCall(id="x", name="t", arguments={"k": "v"})],
        timestamp="2026-01-01T00:00:00",
    )
    restored = Message.from_dict(original.to_dict())
    assert restored.role == original.role
    assert restored.content == original.content
    assert restored.tool_calls[0].name == "t"
    assert restored.tool_calls[0].arguments == {"k": "v"}


def test_provider_response_to_message() -> None:
    response = ProviderResponse(text="answer", tool_calls=[])
    message = response.to_message()
    assert message.role == "assistant"
    assert message.content == "answer"


def test_tool_call_argument_accessor_tolerates_parse_error() -> None:
    call = ToolCall(id="1", name="t", arguments={"__parse_error__": "bad"})
    assert call.argument("command", "fallback") == "fallback"
    clean = ToolCall(id="2", name="t", arguments={"command": "ls"})
    assert clean.argument("command") == "ls"


# -- registry / factory ----------------------------------------------------

def test_registry_builds_each_type(config: Config) -> None:
    assert isinstance(
        build_provider(config.resolve_provider("openai")), OpenAICompatibleProvider
    )
    assert isinstance(
        build_provider(config.resolve_provider("anthropic")), AnthropicProvider
    )
    assert isinstance(
        build_provider(config.resolve_provider("ollama")), OllamaProvider
    )


def test_unknown_provider_type_raises(config: Config) -> None:
    config.set("providers", "weird", {"type": "telepathy", "model": "m", "base_url": "http://x"})
    spec = config.resolve_provider("weird")
    with pytest.raises(ConfigError):
        build_provider(spec)


def test_provider_from_config_uses_default(config: Config) -> None:
    provider = provider_from_config(config)
    assert provider.name == "openai"
    assert provider.model


def test_aliases_resolve_to_openai_compatible(config: Config) -> None:
    config.set(
        "providers",
        "openrouter",
        {"type": "openrouter", "base_url": "https://openrouter.ai/api/v1", "model": "some/model"},
    )
    provider = build_provider(config.resolve_provider("openrouter"))
    assert isinstance(provider, OpenAICompatibleProvider)


def test_describe_mentions_model(config: Config) -> None:
    provider = build_provider(config.resolve_provider("openai"))
    assert "openai" in provider.describe()
    assert provider.model in provider.describe()


# -- anthropic translation -------------------------------------------------

def test_anthropic_translates_system_and_tool_results() -> None:
    messages = [
        Message(role="system", content="you are helpful"),
        Message(role="user", content="run ls"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="tu_1", name="shell", arguments={"command": "ls"})],
        ),
        Message(role="tool", content="file.txt", tool_call_id="tu_1", name="shell"),
    ]
    system_text, translated = AnthropicProvider._translate(messages)

    assert system_text == "you are helpful"
    # System is pulled out of the message list entirely.
    assert all(m["role"] != "system" for m in translated)
    # The tool result becomes a user-role tool_result block.
    tool_block = translated[-1]
    assert tool_block["role"] == "user"
    assert tool_block["content"][0]["type"] == "tool_result"
    assert tool_block["content"][0]["tool_use_id"] == "tu_1"
    # The assistant turn carries a tool_use block.
    assistant_blocks = translated[-2]["content"]
    assert any(b["type"] == "tool_use" for b in assistant_blocks)


def test_anthropic_translates_tool_schemas() -> None:
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "run a command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            },
        }
    ]
    converted = AnthropicProvider._translate_tools(openai_tools)
    assert converted[0]["name"] == "shell"
    assert "input_schema" in converted[0]
    assert "parameters" not in converted[0]


# -- health checks ---------------------------------------------------------

def test_openai_health_check_without_base_url() -> None:
    provider = OpenAICompatibleProvider(name="x", base_url="", model="m")
    ok, message = provider.health_check()
    assert ok is False
    assert "base_url" in message


def test_openai_health_check_without_model() -> None:
    provider = OpenAICompatibleProvider(name="x", base_url="http://localhost:1/v1", model="")
    ok, message = provider.health_check()
    assert ok is False
    assert "model" in message


def test_anthropic_health_check_without_key() -> None:
    provider = AnthropicProvider(name="a", base_url="https://api.anthropic.com", model="m", api_key="")
    ok, message = provider.health_check()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in message


def test_ollama_health_check_unreachable_returns_friendly_message() -> None:
    provider = OllamaProvider(name="o", base_url="http://127.0.0.1:9", model="llama3.1")
    ok, message = provider.health_check()
    assert ok is False
    assert "ollama serve" in message
