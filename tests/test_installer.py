"""Tests for installer/onboarding hardening."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from ackstreet.installer.cli import build_parser, main as installer_main
from ackstreet.installer.ui.validators import (
    validate_anthropic_key,
    validate_env_var_name,
    validate_non_empty,
    validate_openai_key,
    validate_telegram_token,
    validate_url,
    validate_user_id,
)
from ackstreet.installer.ui.config_writer import ConfigWriter
from ackstreet.installer.onboarding.steps.messaging import MessagingSetupStep
from ackstreet.installer.onboarding.steps.llm_provider import LLMProviderStep
from ackstreet.installer.onboarding.orchestrator import OnboardingOptions, OnboardingOrchestrator


class TestValidators:
    def test_key_and_url_validators(self):
        assert validate_openai_key("sk-proj-ABC123XYZ123ABC123XYZ123")
        assert not validate_openai_key("invalid")
        assert validate_anthropic_key("sk-ant-ABC123XYZ123ABC123XYZ123ABC123")
        assert not validate_anthropic_key("sk-proj-1")
        assert validate_telegram_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg")
        assert not validate_telegram_token("short")
        assert validate_url("https://openrouter.ai/api/v1")
        assert not validate_url("openrouter.ai")

    def test_misc_validators(self):
        assert validate_user_id("12345")
        assert not validate_user_id("user")
        assert validate_env_var_name("OPENROUTER_API_KEY")
        assert not validate_env_var_name("bad-var")
        assert validate_non_empty("x")
        assert not validate_non_empty("   ")


class TestConfigWriter:
    def test_write_config_and_env(self, tmp_path):
        config_file = tmp_path / "config.toml"
        env_file = tmp_path / ".env"

        ConfigWriter.write_config(
            config_file,
            {
                "agent": {"provider": "custom", "model": "qwen/qwen3-32b:free"},
                "providers": {
                    "custom": {
                        "type": "openai",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "model": "qwen/qwen3-32b:free",
                    }
                },
            },
        )
        ConfigWriter.write_env_file(env_file, {"OPENROUTER_API_KEY": "sk-or-test"})

        assert config_file.exists()
        assert env_file.exists()
        content = config_file.read_text(encoding="utf-8")
        assert "https://openrouter.ai/api/v1" in content
        assert "OPENROUTER_API_KEY" in content
        assert "OPENROUTER_API_KEY=sk-or-test" in env_file.read_text(encoding="utf-8")


class TestMessagingSetup:
    def test_skip_messaging_non_interactive(self, tmp_path):
        ui = Mock()
        ui.info = Mock()
        step = MessagingSetupStep(ui, tmp_path / ".env")
        assert step.execute(non_interactive=True) is None

    def test_verify_telegram_token_mocked_http(self):
        fake = Mock()
        fake.read.return_value = b'{"ok": true, "result": {"id": 1}}'
        cm = Mock()
        cm.__enter__ = Mock(return_value=fake)
        cm.__exit__ = Mock(return_value=False)
        with patch("ackstreet.installer.onboarding.steps.messaging.request.urlopen", return_value=cm):
            assert MessagingSetupStep._verify_telegram_token(
                "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg"
            ) is True


class TestProviderSetup:
    def test_openrouter_setup_writes_env(self, tmp_path):
        ui = Mock()
        ui.info = Mock()
        ui.instruction = Mock()
        ui.success = Mock()
        ui.prompt.side_effect = ["sk-or-test", "qwen/qwen3-32b:free"]

        config = {}
        step = LLMProviderStep(ui, tmp_path / ".env")
        ok = step._setup_openrouter(config, model=None, api_key=None, api_key_env=None)

        assert ok is True
        assert config["agent"]["provider"] == "custom"
        assert config["providers"]["custom"]["base_url"] == "https://openrouter.ai/api/v1"
        assert "sk-or-test" in (tmp_path / ".env").read_text(encoding="utf-8")


class TestInstallerCli:
    def test_parser_options(self):
        parser = build_parser()
        args = parser.parse_args([
            "--non-interactive",
            "--provider",
            "openrouter",
            "--model",
            "qwen/qwen3-32b:free",
            "--gateway",
            "none",
        ])
        assert args.non_interactive is True
        assert args.provider == "openrouter"
        assert args.model == "qwen/qwen3-32b:free"
        assert args.gateway == "none"

    def test_main_returns_int(self):
        with patch("ackstreet.installer.cli.OnboardingOrchestrator") as orchestrator_cls:
            orchestrator = orchestrator_cls.return_value
            orchestrator.run.return_value = True
            code = installer_main(["--non-interactive", "--provider", "openrouter", "--gateway", "none"])
            assert code == 0


class TestOrchestratorExistingConfig:
    def test_keep_existing_exits_without_modifying(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACKSTREET_HOME", str(tmp_path))
        (tmp_path / "config.toml").write_text("[agent]\nprovider='openai'\n", encoding="utf-8")

        opts = OnboardingOptions(non_interactive=False)
        o = OnboardingOrchestrator(opts)
        o.ui.menu = Mock(return_value="keep")
        o.ui.warning = Mock()
        o.ui.info = Mock()

        assert o._handle_existing_configuration() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
