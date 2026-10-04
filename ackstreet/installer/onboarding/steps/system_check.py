"""System requirements check step."""

import shutil
import sys
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
        if sys.executable:
            self.ui.success(f"python found at {sys.executable}")
        else:
            self.ui.error("python3 not found")
            return False

        return True

    @staticmethod
    def _has_command(cmd: str) -> bool:
        """Check if command exists."""
        return shutil.which(cmd) is not None
