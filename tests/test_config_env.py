"""Regression tests for two config bugs found while proving the approval gate.

Both were discovered by running the real CLI, not by unit tests:

1. ``OPENAI_BASE_URL`` (and friends) were ignored, so a container or CI job
   could not point a provider at a proxy, a mock server or a self-hosted
   gateway without editing ``config.toml``.
2. ``ackstreet config set agent.approval_allowlist '["ls"]'`` stored the value
   as a *string*, so the gate then matched against individual characters
   (``[``, ``"``, ``l``, ``s`` ...) instead of the intended rule.
"""

from __future__ import annotations

import pytest

from ackstreet.cli import _parse_config_value
from ackstreet.config import Config

# -- 1. provider base_url / model environment overrides --------------------

def test_openai_base_url_env_overrides_config(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8099/v1")
    spec = config.resolve_provider("openai")
    assert spec.base_url == "http://127.0.0.1:8099/v1"


def test_openai_api_base_is_accepted_as_an_alias(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_BASE", "http://alias.local/v1")
    assert config.resolve_provider("openai").base_url == "http://alias.local/v1"


def test_trailing_slash_is_stripped_from_the_env_url(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://x.local/v1/")
    assert config.resolve_provider("openai").base_url == "http://x.local/v1"


def test_config_file_wins_when_no_env_var_is_set(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    assert config.resolve_provider("openai").base_url == "https://api.openai.com/v1"


def test_openai_model_env_overrides_config(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-from-env")
    assert config.resolve_provider("openai").model == "gpt-from-env"


def test_anthropic_base_url_env_overrides_config(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic-proxy.local")
    assert config.resolve_provider("anthropic").base_url == "http://anthropic-proxy.local"


def test_ollama_host_env_overrides_config(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
    assert config.resolve_provider("ollama").base_url == "http://gpu-box:11434"


def test_env_override_does_not_leak_to_other_providers(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://only-openai.local/v1")
    assert config.resolve_provider("anthropic").base_url == "https://api.anthropic.com"


# -- 2. config set value parsing -------------------------------------------

def test_json_list_is_parsed_not_stored_as_text() -> None:
    parsed = _parse_config_value('["ls", "git status*"]')
    assert parsed == ["ls", "git status*"]
    assert isinstance(parsed, list)


def test_json_object_is_parsed() -> None:
    assert _parse_config_value('{"a": 1}') == {"a": 1}


def test_empty_json_list_round_trips() -> None:
    assert _parse_config_value("[]") == []


def test_booleans_and_numbers_are_typed() -> None:
    assert _parse_config_value("true") is True
    assert _parse_config_value("false") is False
    assert _parse_config_value("3") == 3
    assert _parse_config_value("0.5") == 0.5


def test_plain_strings_stay_strings() -> None:
    assert _parse_config_value("ask") == "ask"
    assert _parse_config_value("gpt-4o-mini") == "gpt-4o-mini"


def test_malformed_json_falls_back_to_the_raw_string() -> None:
    # Must not raise; a bad value should be stored verbatim so the user can see it.
    assert _parse_config_value("[not json") == "[not json"


def test_allowlist_survives_a_config_round_trip(
    config: Config, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end shape of the bug: set, save, reload, and match."""
    from ackstreet.config import load_config
    from ackstreet.safety import ApprovalPolicy

    config.set("agent", "approval_mode", "allowlist")
    config.set("agent", "approval_allowlist", _parse_config_value('["write_file:*.txt"]'))
    config.save()

    reloaded = load_config(config.path)
    policy = ApprovalPolicy(reloaded)

    assert policy.allowlist == ["write_file:*.txt"]
    assert policy.review("write_file", {"path": "notes.txt"}).approved is True
    assert policy.review("write_file", {"path": "notes.md"}).approved is False
