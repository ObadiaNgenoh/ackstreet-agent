"""LLM provider configuration step."""

from typing import Dict, Any
from pathlib import Path
from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.validators import (
    validate_openai_key,
    validate_anthropic_key,
    validate_url,
)
from ackstreet.installer.ui.config_writer import ConfigWriter


class LLMProviderStep:
    """Configure LLM backend."""

    def __init__(self, ui: Terminal, env_path: Path):
        self.ui = ui
        self.env_path = env_path

    def execute(self, config: Dict[str, Any]) -> bool:
        """Set up LLM provider."""
        providers = [
            (
                "OpenAI (GPT-4o, GPT-4o-mini) - $0.15-3/1M tokens",
                "openai",
            ),
            ("Anthropic (Claude 3.5 Sonnet) - $3-15/1M tokens", "anthropic"),
            ("Local Ollama (Free, on-device, 8GB+ RAM required)", "ollama"),
            ("Other OpenAI-compatible (LM Studio, vLLM, Azure)", "custom"),
        ]

        provider = self.ui.menu("Which LLM provider do you want to use?", providers)

        if provider == "openai":
            return self._setup_openai(config)
        elif provider == "anthropic":
            return self._setup_anthropic(config)
        elif provider == "ollama":
            return self._setup_ollama(config)
        elif provider == "custom":
            return self._setup_custom(config)

        return False

    def _setup_openai(self, config: Dict[str, Any]) -> bool:
        """Interactive OpenAI setup."""
        self.ui.info("OpenAI Setup")

        self.ui.instruction(
            [
                "1. Go to https://platform.openai.com/account/api-keys",
                "2. Click 'Create new secret key'",
                "3. Copy the key (it starts with 'sk-')",
                "4. Paste it below (will be stored securely in ~/.ackstreet/.env)",
            ]
        )

        api_key = self.ui.prompt(
            "Paste your OpenAI API key",
            is_secret=True,
            validator=lambda x: validate_openai_key(x),
        )

        # Model selection
        models = [
            ("GPT-4o (Most capable, slower, $15/1M input tokens)", "gpt-4o"),
            ("GPT-4o mini (Fast & cheap, $0.15/1M input tokens)", "gpt-4o-mini"),
            ("GPT-4 Turbo (Older model, $10/1M input tokens)", "gpt-4-turbo"),
        ]

        model = self.ui.menu("Which model?", models)

        if "agent" not in config:
            config["agent"] = {}
        config["agent"]["provider"] = "openai"
        config["agent"]["model"] = model

        if "providers" not in config:
            config["providers"] = {}
        config["providers"]["openai"] = {
            "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model": model,
        }

        # Store the key in .env file
        ConfigWriter.write_env_file(self.env_path, {"OPENAI_API_KEY": api_key})
        self.ui.success(f"OpenAI configured with {model}")
        self.ui.info("API key stored securely in ~/.ackstreet/.env")
        return True

    def _setup_anthropic(self, config: Dict[str, Any]) -> bool:
        """Interactive Anthropic setup."""
        self.ui.info("Anthropic (Claude) Setup")

        self.ui.instruction(
            [
                "1. Go to https://console.anthropic.com/account/keys",
                "2. Click 'Create Key'",
                "3. Copy the key (it starts with 'sk-ant-')",
                "4. Paste it below",
            ]
        )

        api_key = self.ui.prompt(
            "Paste your Anthropic API key",
            is_secret=True,
            validator=lambda x: validate_anthropic_key(x),
        )

        if "agent" not in config:
            config["agent"] = {}
        config["agent"]["provider"] = "anthropic"
        config["agent"]["model"] = "claude-3-5-sonnet-20241022"

        if "providers" not in config:
            config["providers"] = {}
        config["providers"]["anthropic"] = {
            "type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-3-5-sonnet-20241022",
        }

        ConfigWriter.write_env_file(self.env_path, {"ANTHROPIC_API_KEY": api_key})
        self.ui.success("Anthropic (Claude) configured")
        self.ui.info("API key stored securely in ~/.ackstreet/.env")
        return True

    def _setup_ollama(self, config: Dict[str, Any]) -> bool:
        """Interactive Ollama setup."""
        self.ui.warning("Ollama requires 8GB+ RAM for most models")

        is_local = self.ui.confirm(
            "Is Ollama running on this machine?", default=True
        )

        if is_local:
            base_url = "http://localhost:11434"
            self.ui.instruction(
                [
                    "Install Ollama from https://ollama.com",
                    "In another terminal, run: ollama serve",
                    "Then pull a model: ollama pull llama3.1",
                ]
            )
        else:
            host = self.ui.prompt(
                "What is the Ollama server address?",
                default="http://192.168.1.100:11434",
                validator=lambda x: validate_url(x),
            )
            base_url = host

        models = [
            ("Llama 3.1 (7B, ~4.7GB, balanced)", "llama3.1"),
            ("Llama 3.1:8b (8B, ~5GB, faster)", "llama3.1:8b"),
            ("Mistral (7B, ~4GB, fastest)", "mistral"),
            ("Other (enter model name)", "custom"),
        ]

        model = self.ui.menu("Which model?", models)

        if model == "custom":
            model = self.ui.prompt("Model name (e.g., llama2:13b)")

        if "agent" not in config:
            config["agent"] = {}
        config["agent"]["provider"] = "ollama"
        config["agent"]["model"] = model

        if "providers" not in config:
            config["providers"] = {}
        config["providers"]["ollama"] = {
            "type": "openai",  # Ollama is OpenAI-compatible
            "base_url": base_url,
            "api_key_env": "",
            "model": model,
        }

        self.ui.success(f"Ollama configured: {model} at {base_url}")
        self.ui.warning("Remember to run: ollama serve &")
        return True

    def _setup_custom(self, config: Dict[str, Any]) -> bool:
        """Setup custom OpenAI-compatible endpoint."""
        self.ui.info("Custom OpenAI-Compatible Setup")

        self.ui.instruction(
            [
                "Examples:",
                "  - Azure OpenAI: https://your-resource.openai.azure.com/v1",
                "  - LM Studio: http://localhost:8000/v1",
                "  - vLLM: http://localhost:8000/v1",
            ]
        )

        base_url = self.ui.prompt(
            "Server base URL",
            validator=lambda x: validate_url(x),
        )
        api_key = self.ui.prompt("API key", is_secret=True)
        model = self.ui.prompt("Model name")

        if "agent" not in config:
            config["agent"] = {}
        config["agent"]["provider"] = "custom"
        config["agent"]["model"] = model

        if "providers" not in config:
            config["providers"] = {}
        config["providers"]["custom"] = {
            "type": "openai",
            "base_url": base_url,
            "api_key_env": "CUSTOM_API_KEY",
            "model": model,
        }

        ConfigWriter.write_env_file(self.env_path, {"CUSTOM_API_KEY": api_key})
        self.ui.success(f"Custom provider configured: {base_url}")
        self.ui.info("API key stored securely in ~/.ackstreet/.env")
        return True
