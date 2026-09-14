"""Tests for configuration loading, merging and provider resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from ackstreet.config import DEFAULTS, Config, _dump_toml


def test_defaults_are_loaded(config: Config) -> None:
    assert config.get("agent", "provider") == "openai"
    assert config.get("tools", "allow_shell") is True
    assert config.get("agent", "max_steps") == 25


def test_toml_file_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACKSTREET_HOME", str(tmp_path / "home"))
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[agent]\nprovider = \"ollama\"\nmax_steps = 7\n", encoding="utf-8"
    )
    cfg = Config.load(cfg_file)
    assert cfg.get("agent", "provider") == "ollama"
    assert cfg.get("agent", "max_steps") == 7
    # Untouched defaults survive the merge.
    assert cfg.get("agent", "name") == "Ackstreet"


def test_env_var_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACKSTREET_HOME", str(tmp_path / "home"))
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[agent]\nmax_steps = 7\n", encoding="utf-8")
    monkeypatch.setenv("ACKSTREET_AGENT_MAX_STEPS", "11")
    cfg = Config.load(cfg_file)
    assert cfg.get("agent", "max_steps") == 11


def test_env_var_type_coercion(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACKSTREET_TOOLS_ALLOW_SHELL", "false")
    cfg = Config.load(config.path)
    assert cfg.get("tools", "allow_shell") is False


def test_roundtrip_toml(tmp_path: Path) -> None:
    payload = {
        "agent": {"name": "T", "max_steps": 3, "temperature": 0.5},
        "tools": {"allow_shell": True, "blocked_commands": ["rm -rf /", "mkfs"]},
    }
    text = _dump_toml(payload)
    path = tmp_path / "roundtrip.toml"
    path.write_text(text, encoding="utf-8")

    import tomllib

    with open(path, "rb") as handle:
        loaded = tomllib.load(handle)

    assert loaded["agent"]["name"] == "T"
    assert loaded["agent"]["max_steps"] == 3
    assert loaded["tools"]["allow_shell"] is True
    assert loaded["tools"]["blocked_commands"] == ["rm -rf /", "mkfs"]


def test_resolve_provider_default(config: Config) -> None:
    spec = config.resolve_provider()
    assert spec.name == "openai"
    assert spec.type == "openai"
    assert spec.model  # comes from the defaults


def test_resolve_provider_unknown_raises(config: Config) -> None:
    with pytest.raises(KeyError):
        config.resolve_provider("does-not-exist")


def test_agent_model_overrides_provider_model(config: Config) -> None:
    config.set("agent", "model", "override-model")
    spec = config.resolve_provider("openai")
    assert spec.model == "override-model"


def test_provider_key_read_from_env(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    spec = config.resolve_provider("openai")
    assert spec.api_key == "sk-test-123"
    assert spec.has_key is True


def test_ollama_needs_no_key(config: Config) -> None:
    spec = config.resolve_provider("ollama")
    assert spec.type == "ollama"
    assert spec.has_key is True


def test_set_with_dotted_key_nests_properly(config: Config) -> None:
    """Regression: `config set providers.openai.model x` must nest, not flatten.

    A flat key would make the TOML writer emit a duplicate table header, which
    tomllib then rejects — so the config file became unparseable after any
    dotted `config set`.
    """
    config.set("providers", "openai.model", "dotted-model")

    # The value landed inside the nested table, not as a literal dotted key.
    assert config.data["providers"]["openai"]["model"] == "dotted-model"
    assert "openai.model" not in config.data["providers"]

    # And the file round-trips through a real TOML parser.
    config.save()
    reloaded = Config.load(config.path)
    assert reloaded.get("providers", "openai")["model"] == "dotted-model"


def test_set_dotted_key_creates_missing_tables(config: Config) -> None:
    config.set("providers", "brandnew.base_url", "http://localhost:1234/v1")
    config.set("providers", "brandnew.model", "local")
    config.save()

    reloaded = Config.load(config.path)
    assert reloaded.get("providers", "brandnew")["base_url"] == "http://localhost:1234/v1"
    assert reloaded.resolve_provider("brandnew").model == "local"


def test_dump_toml_quoting_preserves_dotted_keys(tmp_path: Path) -> None:
    """A literal dotted key must be quoted so TOML keeps it flat."""
    import tomllib

    text = _dump_toml({"providers": {"openai.base_url": "http://x"}})
    parsed = tomllib.loads(text)
    assert parsed["providers"]["openai.base_url"] == "http://x"


def test_save_and_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACKSTREET_HOME", str(tmp_path / "home"))
    cfg = Config.load()
    cfg.set("agent", "name", "Roundtrip")
    cfg.save()

    reloaded = Config.load(cfg.path)
    assert reloaded.get("agent", "name") == "Roundtrip"


def test_ensure_dirs_creates_layout(config: Config) -> None:
    config.ensure_dirs()
    for directory in (
        config.root,
        config.skills_dir,
        config.memory_dir,
        config.sessions_dir,
        config.workspace,
    ):
        assert directory.exists()


def test_system_prompt_falls_back_to_default(config: Config) -> None:
    prompt = config.system_prompt()
    assert "Ackstreet" in prompt


def test_defaults_are_not_mutated_by_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACKSTREET_HOME", str(tmp_path / "home"))
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[agent]\nmax_steps = 999\n", encoding="utf-8")
    Config.load(cfg_file)
    assert DEFAULTS["agent"]["max_steps"] == 25, "DEFAULTS must not be mutated"
