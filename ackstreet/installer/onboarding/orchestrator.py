"""Guided setup flow for new installations."""

from typing import Optional, Dict, Any
from pathlib import Path
import sys

from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.onboarding.steps.system_check import SystemCheckStep
from ackstreet.installer.onboarding.steps.messaging import MessagingSetupStep
from ackstreet.installer.onboarding.steps.llm_provider import LLMProviderStep
from ackstreet.installer.onboarding.steps.security import SecurityStep
from ackstreet.installer.onboarding.steps.startup import StartupStep


class OnboardingOrchestrator:
    """Guided setup flow for new installations."""

    def __init__(self, os_name: str):
        self.os_name = os_name
        self.ui = Terminal()
        self.config: Dict[str, Any] = {}
        self.config_path = Path.home() / ".ackstreet" / "config.toml"
        self.env_path = Path.home() / ".ackstreet" / ".env"

    def run(self) -> bool:
        """Run complete onboarding flow."""
        try:
            self.ui.banner("ACKSTREET AGENT — Setup Wizard")

            # Step 1: System Check
            self.ui.section("1. System Requirements")
            system_check = SystemCheckStep(self.os_name, self.ui)
            if not system_check.execute():
                self.ui.error("System check failed")
                return False
            self.ui.success("System check passed")

            # Step 2: Choose Messaging Gateway (optional)
            self.ui.section("2. Messaging Gateway (Optional)")
            messaging = MessagingSetupStep(self.ui)
            messaging_config = messaging.execute()
            if messaging_config:
                self.config["connectors"] = messaging_config

            # Step 3: LLM Provider
            self.ui.section("3. Language Model Provider")
            llm = LLMProviderStep(self.ui, self.env_path)
            if not llm.execute(self.config):
                self.ui.error("LLM provider configuration failed")
                return False

            # Step 4: Security Settings
            self.ui.section("4. Security & Permissions")
            security = SecurityStep(self.ui, self.config)
            security.execute()

            # Step 5: Write config and health check
            self.ui.section("5. Finalizing Installation")
            startup = StartupStep(self.ui, self.config_path, self.env_path)
            if not startup.execute(self.config):
                self.ui.error("Startup failed")
                return False

            self.ui.success("✓ ACKSTREET AGENT is ready!")
            self._show_next_steps()
            return True

        except KeyboardInterrupt:
            self.ui.warning("Setup cancelled by user")
            return False
        except Exception as e:
            self.ui.error(f"Unexpected error: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _show_next_steps(self):
        """Display post-setup instructions."""
        self.ui.info("\nNext Steps:")
        self.ui.print(
            """
  1. Start chatting:
     ackstreet chat

  2. Run a single task:
     ackstreet run "your task here"

  3. View your learned skills:
     ackstreet skills list

  4. Check system status:
     ackstreet doctor
        """
        )


if __name__ == "__main__":
    os_name = sys.argv[1] if len(sys.argv) > 1 else "linux_generic"
    orchestrator = OnboardingOrchestrator(os_name)
    sys.exit(0 if orchestrator.run() else 1)
