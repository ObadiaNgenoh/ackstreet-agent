"""System requirements check step."""

import subprocess
from ackstreet.installer.ui.terminal import Terminal


class SystemCheckStep:
    """Verify system requirements."""

    def __init__(self, os_name: str, ui: Terminal):
        self.os_name = os_name
        self.ui = ui

    def execute(self) -> bool:
        """Run system checks."""
        self.ui.info("Checking dependencies...")

        # Check git
        if self._has_command("git"):
            self.ui.success("git found")
        else:
            self.ui.error("git not found")
            return False

        # Check curl
        if self._has_command("curl"):
            self.ui.success("curl found")
        else:
            self.ui.warning("curl not found (optional, but recommended)")

        # Check Python
        if self._has_command("python3"):
            self.ui.success("python3 found")
        else:
            self.ui.error("python3 not found")
            return False

        return True

    @staticmethod
    def _has_command(cmd: str) -> bool:
        """Check if command exists."""
        try:
            subprocess.run(
                ["which", cmd],
                capture_output=True,
                check=True,
                timeout=2,
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
