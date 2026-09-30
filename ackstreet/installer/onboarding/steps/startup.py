"""Startup and finalization step."""

from typing import Dict, Any
from pathlib import Path
import os
from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.config_writer import ConfigWriter


class StartupStep:
    """Write config and run health check."""

    def __init__(self, ui: Terminal, config_path: Path, env_path: Path):
        self.ui = ui
        self.config_path = config_path
        self.env_path = env_path

    def execute(self, config: Dict[str, Any]) -> bool:
        """Finalize installation."""
        # Set defaults if not already set
        if "agent" not in config:
            config["agent"] = {}
        if "tools" not in config:
            config["tools"] = {}
        if "memory" not in config:
            config["memory"] = {}
        if "skills" not in config:
            config["skills"] = {}

        # Apply defaults
        agent_defaults = {
            "name": "Ackstreet",
            "max_steps": 25,
            "temperature": 0.2,
            "context_messages": 40,
            "auto_curate": True,
            "auto_load_skill": True,
            "approval_mode": "auto",
        }
        for key, value in agent_defaults.items():
            if key not in config["agent"]:
                config["agent"][key] = value

        tool_defaults = {
            "allow_shell": True,
            "allow_web": True,
            "allow_python": True,
            "shell_timeout": 60,
            "web_timeout": 30,
            "max_output_chars": 20000,
        }
        for key, value in tool_defaults.items():
            if key not in config["tools"]:
                config["tools"][key] = value

        # Memory and skills defaults
        memory_defaults = {"enabled": True, "recall_limit": 8}
        for key, value in memory_defaults.items():
            if key not in config["memory"]:
                config["memory"][key] = value

        skills_defaults = {"enabled": True, "min_steps_to_curate": 3}
        for key, value in skills_defaults.items():
            if key not in config["skills"]:
                config["skills"][key] = value

        try:
            self.ui.info(f"Writing configuration to {self.config_path}")
            ConfigWriter.write_config(self.config_path, config)
            self.ui.success("Configuration saved")

            # Load .env file so API keys are available
            self._load_env_file()
            self.ui.success("Environment variables loaded")

            # Show what was configured
            self.ui.print(
                f"""
✓ Configuration complete:
  - Agent name: {config.get('agent', {}).get('name', 'Ackstreet')}
  - LLM Provider: {config.get('agent', {}).get('provider', 'Not configured')}
  - Model: {config.get('agent', {}).get('model', 'Default')}
  - Shell allowed: {config.get('tools', {}).get('allow_shell', True)}
  - Web search allowed: {config.get('tools', {}).get('allow_web', True)}
  - Python execution allowed: {config.get('tools', {}).get('allow_python', True)}
            """
            )

            if config.get("connectors", {}).get("enabled"):
                if "telegram" in config.get("connectors", {}):
                    self.ui.print("  - Telegram connector configured")
                if "whatsapp" in config.get("connectors", {}):
                    self.ui.print("  - WhatsApp connector configured")

            return True

        except Exception as e:
            self.ui.error(f"Failed to write configuration: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _load_env_file(self):
        """Load environment variables from .env file."""
        if not self.env_path.exists():
            return

        try:
            for line in self.env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    # Only set if not already in environment
                    if key not in os.environ:
                        os.environ[key] = value
        except Exception as e:
            self.ui.warning(f"Could not load .env file: {e}")
