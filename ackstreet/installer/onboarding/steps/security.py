"""Security configuration step."""

from typing import Dict, Any
from ackstreet.installer.ui.terminal import Terminal


class SecurityStep:
    """Configure security settings."""

    def __init__(self, ui: Terminal, config: Dict[str, Any]):
        self.ui = ui
        self.config = config

    def execute(self):
        """Run security configuration."""
        self.ui.info("Reviewing security settings...")

        if "tools" not in self.config:
            self.config["tools"] = {}

        # Shell access approval
        allow_shell = self.ui.confirm(
            "Allow shell command execution?",
            default=True,
        )
        self.config["tools"]["allow_shell"] = allow_shell

        if allow_shell:
            approval_mode = self.ui.menu(
                "Shell approval mode:",
                [
                    (
                        "Auto - approve dangerous commands without asking",
                        "auto",
                    ),
                    ("Ask - prompt before each dangerous command", "ask"),
                    ("Allowlist - only specific patterns approved", "allowlist"),
                ],
            )
            if "agent" not in self.config:
                self.config["agent"] = {}
            self.config["agent"]["approval_mode"] = approval_mode

        # Web search
        allow_web = self.ui.confirm(
            "Allow web search and page fetching?",
            default=True,
        )
        self.config["tools"]["allow_web"] = allow_web

        # Python execution
        allow_python = self.ui.confirm(
            "Allow Python code execution?",
            default=True,
        )
        self.config["tools"]["allow_python"] = allow_python

        self.ui.success("Security settings configured")
