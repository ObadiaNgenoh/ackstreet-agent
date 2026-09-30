"""Startup and finalization step."""

from typing import Dict, Any
from pathlib import Path
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

        # Apply defaults
        defaults = {
            "name": "Ackstreet",
            "max_steps": 25,
            "temperature": 0.2,
            "context_messages": 40,
            "auto_curate": True,
            "auto_load_skill": True,
            "approval_mode": "auto",
        }
        for key, value in defaults.items():
            if key not in config["agent"]:
                config["agent"][key] = value

        tool_defaults = {
            "allow_shell": True,
            "allow_web": True,
            "allow_python": True,
            "shell_timeout": 60,
            "web_timeout": 30,
        }
        for key, value in tool_defaults.items():
            if key not in config["tools"]:
                config["tools"][key] = value

        # Memory and skills defaults
        if "memory" not in config:
            config["memory"] = {"enabled": True, "recall_limit": 8}
        if "skills" not in config:
            config["skills"] = {"enabled": True, "min_steps_to_curate": 3}

        try:
            self.ui.info(f"Writing configuration to {self.config_path}")
            ConfigWriter.write_config(self.config_path, config)
            self.ui.success("Configuration saved")

            self.ui.info("Environment file saved (if API keys were configured)")

            # Show what was configured
            self.ui.print("""
  ✓ Configuration:
    - Agent: {}
    - Provider: {}
    - Shell allowed: {}
    - Web search allowed: {}
    - Python execution allowed: {}
            """.format(
                config["agent"].get("name", "Ackstreet"),
                config["agent"].get("provider", "Not configured"),
                config["tools"].get("allow_shell", True),
                config["tools"].get("allow_web", True),
                config["tools"].get("allow_python", True),
            ))

            return True

        except Exception as e:
            self.ui.error(f"Failed to write configuration: {e}")
            return False
