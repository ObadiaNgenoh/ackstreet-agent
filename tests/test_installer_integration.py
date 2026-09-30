#!/usr/bin/env python3
"""Integration tests for the complete onboarding flow.

These tests verify the end-to-end behavior of the installer in different scenarios.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import json


def test_installer_entry_point():
    """Test that ackstreet-install entry point exists and runs."""
    result = subprocess.run(
        [sys.executable, "-m", "ackstreet.installer.cli", "ubuntu"],
        input="n\nn\n",  # Skip messaging, exit at provider selection
        text=True,
        capture_output=True,
        timeout=5,
    )
    # Should at least start the wizard
    assert "Setup Wizard" in result.stdout or "provider" in result.stdout


def test_config_generation():
    """Test that config.toml is generated correctly."""
    from ackstreet.installer.ui.config_writer import ConfigWriter

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"
        config = {
            "agent": {
                "name": "TestAgent",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "max_steps": 25,
                "auto_curate": True,
            },
            "providers": {
                "openai": {
                    "type": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "api_key_env": "OPENAI_API_KEY",
                    "model": "gpt-4o-mini",
                }
            },
            "tools": {
                "allow_shell": True,
                "allow_web": True,
                "allow_python": True,
                "shell_timeout": 60,
            },
        }

        ConfigWriter.write_config(config_path, config)

        # Verify file exists and is readable
        assert config_path.exists()
        content = config_path.read_text()

        # Verify TOML syntax is valid
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        parsed = tomllib.loads(content)
        assert parsed["agent"]["name"] == "TestAgent"
        assert parsed["tools"]["allow_shell"] is True
        assert parsed["tools"]["shell_timeout"] == 60


def test_env_file_generation():
    """Test that .env file is generated with proper permissions."""
    from ackstreet.installer.ui.config_writer import ConfigWriter
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        keys = {
            "OPENAI_API_KEY": "sk-proj-test123",
            "CUSTOM_API_KEY": "my-secret-key",
        }

        ConfigWriter.write_env_file(env_path, keys)

        # Verify file exists
        assert env_path.exists()

        # Verify permissions are restrictive (0o600)
        mode = env_path.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

        # Verify content
        content = env_path.read_text()
        assert "OPENAI_API_KEY=sk-proj-test123" in content
        assert "CUSTOM_API_KEY=my-secret-key" in content


def test_telegram_token_format_validation():
    """Test Telegram token format validation."""
    from ackstreet.installer.ui.validators import validate_telegram_token

    # Valid tokens
    assert validate_telegram_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd") is True
    assert (
        validate_telegram_token(
            "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
        )
        is True
    )

    # Invalid tokens
    assert validate_telegram_token("invalid") is False
    assert validate_telegram_token("123:short") is False
    assert validate_telegram_token("") is False


def test_openai_key_format_validation():
    """Test OpenAI API key format validation."""
    from ackstreet.installer.ui.validators import validate_openai_key

    # Valid keys
    assert validate_openai_key("sk-proj-ABC123XYZ123ABC123XYZ123") is True
    assert (
        validate_openai_key(
            "sk-proj-verylongkeywithlotsofcharacterstopassvalidation"
        )
        is True
    )

    # Invalid keys
    assert validate_openai_key("invalid") is False
    assert validate_openai_key("sk-") is False
    assert validate_openai_key("") is False


def test_config_loading_with_env_override():
    """Test that config loader respects environment variable overrides."""
    import os
    from ackstreet.config import Config

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"

        # Create a basic config
        config_content = """
[agent]
name = "Ackstreet"
provider = "openai"
model = "gpt-4o-mini"

[tools]
allow_shell = true
allow_web = true
        """
        config_path.write_text(config_content)

        # Set environment override
        os.environ["ACKSTREET_AGENT_PROVIDER"] = "ollama"

        try:
            # Load config
            config = Config.load(config_path)

            # Verify override works
            assert config.get("agent", "provider") == "ollama"
        finally:
            # Clean up
            del os.environ["ACKSTREET_AGENT_PROVIDER"]


if __name__ == "__main__":
    print("Running integration tests...")
    test_config_generation()
    print("✓ Config generation")
    test_env_file_generation()
    print("✓ Env file generation")
    test_telegram_token_format_validation()
    print("✓ Telegram token validation")
    test_openai_key_format_validation()
    print("✓ OpenAI key validation")
    test_config_loading_with_env_override()
    print("✓ Config loading with env override")
    print("\nAll integration tests passed!")
