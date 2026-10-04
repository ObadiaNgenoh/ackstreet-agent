"""LLM provider configuration step."""

from __future__ import annotations

from typing import Dict, Any, Optional
from pathlib import Path

from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.validators import (
    validate_openai_key,
    validate_anthropic_key,
    validate_url,
    validate_env_var_name,
    validate_non_empty,
)
from ackstreet.installer.ui.config_writer import ConfigWriter
from ackstreet.config import Config
from ackstreet.providers import provider_from_config


class LLMProviderStep:
    """Configure LLM backend."""

    def __init__(self, ui: Terminal, env_path: Path):
        self.ui = ui
        self.env_path = env_path

    def execute(
        self,
        config: Dict[str, Any],
        non_interactive: bool = False,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key_env: Optional[str] = None,
        api_key: Optional[str] = None,
        skip_doctor: bool = False,
    ) -> bool:
        """Set up LLM provider."""
        selected_provider = (provider or "").strip().lower()
        providers = [
            ("OpenRouter (OpenAI-compatible)", "openrouter"),
            ("OpenAI (GPT models)", "openai"),
            ("Anthropic (Claude models)", "anthropic"),
            ("Local Ollama (Free, on-device)", "ollama"),
            ("Other OpenAI-compatible endpoint", "custom"),
        ]

        if not selected_provider:
            if non_interactive:
                selected_provider = "openrouter"
            else:
                selected_provider = self.ui.menu(
                    "Which LLM provider do you want to use?", providers
                )

        if selected_provider == "openrouter":
            ok = self._setup_openrouter(config, model=model, api_key=api_key, api_key_env=api_key_env)
        elif selected_provider == "openai":
            ok = self._setup_openai(config, model=model, api_key=api_key)
        elif selected_provider == "anthropic":
            ok = self._setup_anthropic(config, model=model, api_key=api_key)
        elif selected_provider == "ollama":
            ok = self._setup_ollama(config, model=model)
        elif selected_provider == "custom":
            ok = self._setup_custom(config, model=model, api_key=api_key, api_key_env=api_key_env)
        else:
            self.ui.error(f"Unsupported provider: {selected_provider}")
            return False

        if not ok:
            return False

        if skip_doctor:
            return True

        if non_interactive:
            return True

        should_test = self.ui.confirm("Run an optional connectivity check now?", default=True)
        if should_test:
            return self._run_connectivity_test(config)
        return True

    def _run_connectivity_test(self, config: Dict[str, Any]) -> bool:
        while True:
            cfg = Config(config)
            spec = cfg.resolve_provider()
            provider = provider_from_config(cfg, name=spec.name, timeout=15.0)
            try:
                healthy, message = provider.health_check()
            finally:
                provider.close()

            if healthy:
                self.ui.success(f"Connectivity check passed: {message}")
                return True

            self.ui.warning(f"Connectivity check failed: {message}")
            action = self.ui.menu(
                "What next?",
                [
                    ("Retry connectivity test", "retry"),
                    ("Continue anyway", "continue"),
                    ("Reconfigure provider", "reconfigure"),
                ],
            )
            if action == "retry":
                continue
            if action == "continue":
                return True
            return False

    def _setup_openrouter(self, config: Dict[str, Any], model: Optional[str], api_key: Optional[str], api_key_env: Optional[str]) -> bool:
        self.ui.info("OpenRouter setup")
        self.ui.instruction(
            [
                "Get an API key from https://openrouter.ai/keys",
                "Use the exact model ID from OpenRouter docs.",
                "Examples: qwen/qwen3-32b:free, mistralai/mistral-7b-instruct:free",
            ]
        )
        api_env = (api_key_env or "OPENROUTER_API_KEY").strip() or "OPENROUTER_API_KEY"
        if not validate_env_var_name(api_env):
            self.ui.error("Invalid api-key environment variable name")
            return False
        secret = api_key
        if not secret:
            secret = self.ui.prompt(
                f"Paste your OpenRouter API key ({api_env})",
                is_secret=True,
                validator=lambda x: validate_openai_key(x),
            )
        chosen_model = (model or "").strip()
        if not chosen_model:
            chosen_model = self.ui.prompt(
                "Enter exact OpenRouter model ID",
                default="qwen/qwen3-32b:free",
                validator=validate_non_empty,
            )

        self._apply_provider_config(
            config,
            provider_name="custom",
            provider_type="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key_env=api_env,
            model=chosen_model,
            env_secret={api_env: secret},
        )
        self.ui.success(f"OpenRouter configured with model: {chosen_model}")
        self.ui.info(f"API key stored securely in {self.env_path}")
        return True

    def _setup_openai(self, config: Dict[str, Any], model: Optional[str], api_key: Optional[str]) -> bool:
        """Interactive OpenAI setup."""
        self.ui.info("OpenAI Setup")
        secret = api_key
        if not secret:
            secret = self.ui.prompt(
                "Paste your OpenAI API key",
                is_secret=True,
                validator=lambda x: validate_openai_key(x),
            )

        chosen_model = (model or "").strip()
        if not chosen_model:
            models = [
                ("GPT-4o", "gpt-4o"),
                ("GPT-4o mini", "gpt-4o-mini"),
                ("Enter a custom model", "custom"),
            ]
            chosen_model = self.ui.menu("Which model?", models)
            if chosen_model == "custom":
                chosen_model = self.ui.prompt("Model name", validator=validate_non_empty)

        self._apply_provider_config(
            config,
            provider_name="openai",
            provider_type="openai",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            model=chosen_model,
            env_secret={"OPENAI_API_KEY": secret},
        )
        self.ui.success(f"OpenAI configured with {chosen_model}")
        self.ui.info(f"API key stored securely in {self.env_path}")
        return True

    def _setup_anthropic(self, config: Dict[str, Any], model: Optional[str], api_key: Optional[str]) -> bool:
        """Interactive Anthropic setup."""
        self.ui.info("Anthropic (Claude) Setup")

        secret = api_key
        if not secret:
            secret = self.ui.prompt(
                "Paste your Anthropic API key",
                is_secret=True,
                validator=lambda x: validate_anthropic_key(x),
            )

        chosen_model = (model or "").strip() or "claude-3-5-sonnet-20241022"

        self._apply_provider_config(
            config,
            provider_name="anthropic",
            provider_type="anthropic",
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            model=chosen_model,
            env_secret={"ANTHROPIC_API_KEY": secret},
        )
        self.ui.success("Anthropic (Claude) configured")
        self.ui.info(f"API key stored securely in {self.env_path}")
        return True

    def _setup_ollama(self, config: Dict[str, Any], model: Optional[str]) -> bool:
        """Interactive Ollama setup."""
        self.ui.warning("Ollama requires local model installation")

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
            base_url = self.ui.prompt(
                "What is the Ollama server address?",
                default="http://192.168.1.100:11434",
                validator=lambda x: validate_url(x),
            )

        chosen_model = (model or "").strip()
        if not chosen_model:
            models = [
                ("llama3.1", "llama3.1"),
                ("llama3.1:8b", "llama3.1:8b"),
                ("mistral", "mistral"),
                ("Other (enter model name)", "custom"),
            ]

            chosen_model = self.ui.menu("Which model?", models)
            if chosen_model == "custom":
                chosen_model = self.ui.prompt("Model name", validator=validate_non_empty)

        self._apply_provider_config(
            config,
            provider_name="ollama",
            provider_type="ollama",
            base_url=base_url,
            api_key_env="",
            model=chosen_model,
            env_secret=None,
        )

        self.ui.success(f"Ollama configured: {chosen_model} at {base_url}")
        self.ui.warning("Remember to run: ollama serve")
        return True

    def _setup_custom(
        self,
        config: Dict[str, Any],
        model: Optional[str],
        api_key: Optional[str],
        api_key_env: Optional[str],
    ) -> bool:
        """Setup custom OpenAI-compatible endpoint."""
        self.ui.info("Custom OpenAI-Compatible Setup")

        self.ui.instruction(
            [
                "Examples:",
                "  - OpenRouter: https://openrouter.ai/api/v1",
                "  - LM Studio: http://localhost:8000/v1",
                "  - vLLM: http://localhost:8000/v1",
            ]
        )

        base_url = self.ui.prompt(
            "Server base URL",
            validator=lambda x: validate_url(x),
        )
        chosen_model = (model or "").strip()
        if not chosen_model:
            chosen_model = self.ui.prompt("Model name", validator=validate_non_empty)

        api_env = (api_key_env or "CUSTOM_API_KEY").strip() or "CUSTOM_API_KEY"
        if not validate_env_var_name(api_env):
            self.ui.error("Invalid api-key environment variable name")
            return False

        secret = api_key
        if not secret:
            secret = self.ui.prompt("API key", is_secret=True, validator=validate_non_empty)

        self._apply_provider_config(
            config,
            provider_name="custom",
            provider_type="openai",
            base_url=base_url,
            api_key_env=api_env,
            model=chosen_model,
            env_secret={api_env: secret},
        )

        self.ui.success(f"Custom provider configured: {base_url}")
        self.ui.info(f"API key stored securely in {self.env_path}")
        return True

    def _apply_provider_config(
        self,
        config: Dict[str, Any],
        provider_name: str,
        provider_type: str,
        base_url: str,
        api_key_env: str,
        model: str,
        env_secret: Optional[Dict[str, str]],
    ) -> None:
        config.setdefault("agent", {})
        config["agent"]["provider"] = provider_name
        config["agent"]["model"] = model

        config.setdefault("providers", {})
        config["providers"][provider_name] = {
            "type": provider_type,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "model": model,
        }

        if env_secret:
            ConfigWriter.write_env_file(self.env_path, env_secret)
