"""Startup and finalization step."""

from typing import Dict, Any
from pathlib import Path
from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.config_writer import ConfigWriter
from ackstreet.config import load_env_file


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
            load_env_file(self.env_path)
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

            connectors = config.get("connectors", {})
            if connectors.get("enabled"):
                if "telegram" in connectors:
                    self.ui.print("  - Telegram connector configured")
                if "whatsapp" in connectors:
                    self.ui.print("  - WhatsApp connector configured")
                empty_allowlist = not connectors.get("allowed_user_ids") and not connectors.get("telegram", {}).get("allowed_user_ids")
                if empty_allowlist:
                    self.ui.warning(
                        "Connector allowlist is empty. Anyone who can message the bot may control this agent."
                    )

            return True

        except Exception as e:
            self.ui.error(f"Failed to write configuration: {e}")
            import traceback
            traceback.print_exc()
            return False
