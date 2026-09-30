"""Tests for the interactive onboarding flow."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.validators import (
    validate_openai_key,
    validate_anthropic_key,
    validate_telegram_token,
    validate_user_id,
    validate_url,
)
from ackstreet.installer.ui.config_writer import ConfigWriter
from ackstreet.installer.onboarding.steps.messaging import MessagingSetupStep
from ackstreet.installer.onboarding.steps.llm_provider import LLMProviderStep
from ackstreet.installer.onboarding.steps.security import SecurityStep
from ackstreet.installer.onboarding.steps.startup import StartupStep
from ackstreet.installer.onboarding.orchestrator import OnboardingOrchestrator


class TestValidators:
    """Test input validators."""

    def test_validate_openai_key(self):
        """Test OpenAI key validation."""
        assert validate_openai_key("sk-proj-ABC123XYZ123ABC123XYZ123") is True
        assert validate_openai_key("sk-") is False
        assert validate_openai_key("invalid") is False

    def test_validate_anthropic_key(self):
        """Test Anthropic key validation."""
        assert validate_anthropic_key("sk-ant-ABC123XYZ123ABC123XYZ123ABC123") is True
        assert validate_anthropic_key("sk-") is False
        assert validate_anthropic_key("sk-proj-test") is False

    def test_validate_telegram_token(self):
        """Test Telegram token validation."""
        assert validate_telegram_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg") is True
        assert validate_telegram_token("invalid") is False
        assert validate_telegram_token("123:short") is False

    def test_validate_user_id(self):
        """Test user ID validation."""
        assert validate_user_id("123456789") is True
        assert validate_user_id("abc") is False
        assert validate_user_id("") is False

    def test_validate_url(self):
        """Test URL validation."""
        assert validate_url("http://localhost:11434") is True
        assert validate_url("https://api.openai.com/v1") is True
        assert validate_url("not-a-url") is False
        assert validate_url("") is False


class TestConfigWriter:
    """Test TOML configuration writer."""

    def test_escape_toml_string(self):
        """Test string escaping for TOML."""
        assert ConfigWriter._escape_toml_string('simple') == '"simple"'
        assert ConfigWriter._escape_toml_string('with"quote') == '"with\\\"quote"'
        assert ConfigWriter._escape_toml_string('with\\backslash') == '"with\\\\backslash"'

    def test_format_key_value_scalars(self):
        """Test formatting scalar key-value pairs."""
        assert ConfigWriter._format_key_value('name', 'value') == 'name = "value"'
        assert ConfigWriter._format_key_value('count', 42) == 'count = 42'
        assert ConfigWriter._format_key_value('enabled', True) == 'enabled = true'
        assert ConfigWriter._format_key_value('disabled', False) == 'disabled = false'
        assert ConfigWriter._format_key_value('ratio', 0.5) == 'ratio = 0.5'

    def test_format_key_value_arrays(self):
        """Test formatting array key-value pairs."""
        result = ConfigWriter._format_key_value('items', ['a', 'b', 'c'])
        assert result == 'items = ["a", "b", "c"]'

        result = ConfigWriter._format_key_value('numbers', [1, 2, 3])
        assert result == 'numbers = [1, 2, 3]'

        result = ConfigWriter._format_key_value('flags', [True, False, True])
        assert result == 'flags = [true, false, true]'

    def test_write_config(self, tmp_path):
        """Test writing a complete TOML config."""
        config_file = tmp_path / "config.toml"
        config = {
            "agent": {
                "name": "Ackstreet",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "max_steps": 25,
                "temperature": 0.2,
                "auto_curate": True,
            },
            "tools": {
                "allow_shell": True,
                "allow_web": True,
                "shell_timeout": 60,
            },
        }

        ConfigWriter.write_config(config_file, config)
        assert config_file.exists()

        # Verify content
        content = config_file.read_text()
        assert '[agent]' in content
        assert 'name = "Ackstreet"' in content
        assert 'provider = "openai"' in content
        assert 'max_steps = 25' in content
        assert 'auto_curate = true' in content
        assert '[tools]' in content
        assert 'allow_shell = true' in content
        assert 'shell_timeout = 60' in content

        # Verify permissions
        assert oct(config_file.stat().st_mode)[-3:] == '600'

    def test_write_env_file(self, tmp_path):
        """Test writing a .env file with API keys."""
        env_file = tmp_path / ".env"
        keys = {
            "OPENAI_API_KEY": "sk-proj-test123",
            "ANTHROPIC_API_KEY": "sk-ant-test456",
        }

        ConfigWriter.write_env_file(env_file, keys)
        assert env_file.exists()

        content = env_file.read_text()
        assert "OPENAI_API_KEY=sk-proj-test123" in content
        assert "ANTHROPIC_API_KEY=sk-ant-test456" in content

        # Verify permissions
        assert oct(env_file.stat().st_mode)[-3:] == '600'

    def test_env_file_merge(self, tmp_path):
        """Test that env files merge existing values."""
        env_file = tmp_path / ".env"

        # Write initial keys
        ConfigWriter.write_env_file(env_file, {"KEY1": "value1"})
        assert "KEY1=value1" in env_file.read_text()

        # Add new key
        ConfigWriter.write_env_file(env_file, {"KEY2": "value2"})
        content = env_file.read_text()
        assert "KEY1=value1" in content
        assert "KEY2=value2" in content


class TestTerminalUI:
    """Test terminal UI components."""

    def test_confirm_yes(self):
        """Test yes/no confirmation."""
        ui = Terminal()
        with patch('builtins.input', return_value='y'):
            assert ui.confirm("Continue?", default=False) is True

    def test_confirm_no(self):
        """Test no response."""
        ui = Terminal()
        with patch('builtins.input', return_value='n'):
            assert ui.confirm("Continue?", default=True) is False

    def test_confirm_default(self):
        """Test default value when empty response."""
        ui = Terminal()
        with patch('builtins.input', return_value=''):
            assert ui.confirm("Continue?", default=True) is True

        with patch('builtins.input', return_value=''):
            assert ui.confirm("Continue?", default=False) is False

    def test_menu_selection(self):
        """Test menu selection."""
        ui = Terminal()
        options = [
            ("Option A", "a"),
            ("Option B", "b"),
            ("Option C", "c"),
        ]

        with patch('builtins.input', return_value='2'):
            result = ui.menu("Choose one:", options)
            assert result == "b"

    def test_prompt_with_validation(self):
        """Test prompt with validator."""
        ui = Terminal()
        with patch('builtins.input', side_effect=['invalid', 'sk-proj-valid']):
            with patch.object(ui, 'error'):
                result = ui.prompt(
                    "API key:",
                    validator=lambda x: x.startswith('sk-proj-')
                )
                assert result == 'sk-proj-valid'


class TestMessagingSetup:
    """Test Telegram/WhatsApp setup."""

    def test_skip_messaging_setup(self):
        """Test skipping messaging gateway setup."""
        ui = Mock(spec=Terminal)
        ui.confirm.return_value = False
        ui.info = Mock()

        step = MessagingSetupStep(ui)
        result = step.execute()

        assert result is None
        ui.info.assert_called_with("Skipping messaging setup. You can configure it later.")

    def test_telegram_token_verification(self):
        """Test Telegram token verification."""
        # Valid token format
        assert MessagingSetupStep._verify_telegram_token(
            "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd"
        ) is not None

        # Invalid token format
        assert MessagingSetupStep._verify_telegram_token("invalid") is False


class TestLLMProviderSetup:
    """Test LLM provider configuration."""

    def test_openai_setup(self, tmp_path):
        """Test OpenAI provider setup."""
        ui = Mock(spec=Terminal)
        ui.menu.return_value = "gpt-4o-mini"
        ui.prompt.return_value = "sk-proj-test123"
        ui.success = Mock()
        ui.info = Mock()
        ui.instruction = Mock()

        env_path = tmp_path / ".env"
        step = LLMProviderStep(ui, env_path)

        config = {}
        result = step._setup_openai(config)

        assert result is True
        assert config["agent"]["provider"] == "openai"
        assert config["agent"]["model"] == "gpt-4o-mini"
        assert config["providers"]["openai"]["api_key_env"] == "OPENAI_API_KEY"

    def test_ollama_setup(self, tmp_path):
        """Test Ollama provider setup."""
        ui = Mock(spec=Terminal)
        ui.confirm.return_value = True
        ui.menu.return_value = "llama3.1"
        ui.prompt.return_value = "http://localhost:11434"
        ui.success = Mock()
        ui.warning = Mock()
        ui.instruction = Mock()

        env_path = tmp_path / ".env"
        step = LLMProviderStep(ui, env_path)

        config = {}
        result = step._setup_ollama(config)

        assert result is True
        assert config["agent"]["provider"] == "ollama"
        assert config["agent"]["model"] == "llama3.1"


class TestSecuritySetup:
    """Test security configuration."""

    def test_security_settings(self):
        """Test security settings configuration."""
        ui = Mock(spec=Terminal)
        ui.confirm.side_effect = [True, "ask", True, True]
        ui.menu.return_value = "ask"
        ui.success = Mock()
        ui.info = Mock()

        config = {}
        step = SecurityStep(ui, config)
        step.execute()

        assert config["tools"]["allow_shell"] is True
        assert config["tools"]["allow_web"] is True
        assert config["tools"]["allow_python"] is True
        assert config["agent"]["approval_mode"] == "ask"


class TestStartupStep:
    """Test startup and finalization."""

    def test_startup_writes_config(self, tmp_path):
        """Test that startup writes the config file."""
        ui = Mock(spec=Terminal)
        ui.info = Mock()
        ui.success = Mock()
        ui.print = Mock()

        config_path = tmp_path / "config.toml"
        env_path = tmp_path / ".env"

        step = StartupStep(ui, config_path, env_path)

        config = {
            "agent": {"name": "Ackstreet", "provider": "openai"},
            "tools": {"allow_shell": True},
        }

        result = step.execute(config)

        assert result is True
        assert config_path.exists()
        content = config_path.read_text()
        assert "[agent]" in content
        assert 'name = "Ackstreet"' in content

    def test_startup_loads_env(self, tmp_path):
        """Test that startup loads environment variables."""
        import os

        ui = Mock(spec=Terminal)
        ui.info = Mock()
        ui.success = Mock()
        ui.print = Mock()

        config_path = tmp_path / "config.toml"
        env_path = tmp_path / ".env"

        # Write env file
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("TEST_KEY=test_value\n")
        env_path.chmod(0o600)

        step = StartupStep(ui, config_path, env_path)
        config = {"agent": {"name": "Test"}}
        step.execute(config)

        # Check that env var was loaded
        assert os.environ.get("TEST_KEY") == "test_value"


class TestOnboardingOrchestrator:
    """Test the main onboarding orchestrator."""

    def test_orchestrator_creation(self):
        """Test orchestrator initialization."""
        orchestrator = OnboardingOrchestrator("ubuntu")
        assert orchestrator.os_name == "ubuntu"
        assert orchestrator.config == {}
        assert orchestrator.config_path.name == "config.toml"
        assert orchestrator.env_path.name == ".env"

    def test_orchestrator_keyboard_interrupt(self):
        """Test handling of keyboard interrupt."""
        orchestrator = OnboardingOrchestrator("ubuntu")
        with patch.object(orchestrator.ui, 'banner', side_effect=KeyboardInterrupt()):
            result = orchestrator.run()
            assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
