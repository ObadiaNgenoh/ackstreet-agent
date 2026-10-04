"""Guided setup flow for new and existing installations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
import os

from ackstreet.config import Config, config_path, env_path
from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.onboarding.steps.system_check import SystemCheckStep
from ackstreet.installer.onboarding.steps.messaging import MessagingSetupStep
from ackstreet.installer.onboarding.steps.llm_provider import LLMProviderStep
from ackstreet.installer.onboarding.steps.security import SecurityStep
from ackstreet.installer.onboarding.steps.startup import StartupStep


@dataclass
class OnboardingOptions:
    os_name: str = "linux_generic"
    non_interactive: bool = False
    yes: bool = False
    skip_doctor: bool = False
    provider: str = ""
    model: str = ""
    api_key_env: str = ""
    api_key: str = ""
    gateway: str = ""
    telegram_token: str = ""
    telegram_user_id: str = ""


class OnboardingOrchestrator:
    """Guided setup flow for new installations."""

    def __init__(self, options: OnboardingOptions):
        self.options = options
        self.os_name = options.os_name
        self.ui = Terminal()
        self.config: Dict[str, Any] = {}
        self.config_path = config_path()
        self.env_path = env_path()
        self._kept_existing = False

    def run(self) -> bool:
        """Run complete onboarding flow."""
        try:
            self.ui.banner("ACKSTREET AGENT — Setup Wizard")

            if not self._handle_existing_configuration():
                return self._kept_existing

            # Step 1: System Check
            self.ui.section("1/5. System Requirements")
            system_check = SystemCheckStep(self.os_name, self.ui)
            if not system_check.execute():
                self.ui.error("System check failed")
                return False
            self.ui.success("System check passed")

            # Step 2: Messaging Gateway (optional)
            self.ui.section("2/5. Messaging Gateway (Optional)")
            messaging = MessagingSetupStep(self.ui, self.env_path)
            messaging_config = messaging.execute(
                non_interactive=self.options.non_interactive,
                gateway=self.options.gateway,
                telegram_token=self.options.telegram_token,
                telegram_user_id=self.options.telegram_user_id,
            )
            if messaging_config:
                self.config["connectors"] = messaging_config

            # Step 3: LLM Provider
            self.ui.section("3/5. Language Model Provider")
            llm = LLMProviderStep(self.ui, self.env_path)
            if not llm.execute(
                self.config,
                non_interactive=self.options.non_interactive,
                provider=self.options.provider,
                model=self.options.model,
                api_key_env=self.options.api_key_env,
                api_key=self.options.api_key,
                skip_doctor=self.options.skip_doctor,
            ):
                self.ui.error("LLM provider configuration failed")
                return False

            # Step 4: Security Settings
            self.ui.section("4/5. Security & Permissions")
            security = SecurityStep(self.ui, self.config)
            if self.options.non_interactive and self.options.yes:
                self.config.setdefault("agent", {})["approval_mode"] = "auto"
                self.config.setdefault("tools", {})["allow_shell"] = True
                self.config["tools"]["allow_web"] = True
                self.config["tools"]["allow_python"] = True
            else:
                security.execute()

            # Step 5: Write config and health check
            self.ui.section("5/5. Finalizing Installation")
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

    def _handle_existing_configuration(self) -> bool:
        existing = self.config_path.exists() or self.env_path.exists()
        if not existing:
            return True

        self.ui.warning(
            f"Existing ACKSTREET configuration detected at {self.config_path.parent}"
        )

        if self.options.non_interactive:
            self.ui.info("Non-interactive mode keeps existing config and secrets by default.")
            return True

        action = self.ui.menu(
            "Choose how to continue:",
            [
                ("Keep existing settings and exit", "keep"),
                ("Reconfigure (keep existing files unless changed)", "reconfigure"),
                ("Reset config and secrets", "reset"),
            ],
        )
        if action == "keep":
            self._kept_existing = True
            self.ui.info("Keeping existing configuration. You can rerun `ackstreet-install` anytime.")
            self._show_next_steps(existing_only=True)
            return False

        if action == "reset":
            confirm = self.ui.prompt(
                "Type RESET to confirm deleting existing config and env",
                default="",
            )
            if confirm.strip() != "RESET":
                self.ui.warning("Reset cancelled; leaving existing files unchanged.")
                return False
            for target in (self.config_path, self.env_path):
                if target.exists():
                    target.unlink()
            return True

        # reconfigure: start from existing parsed config if possible
        try:
            cfg = Config.load(self.config_path)
            self.config = cfg.data
        except Exception:
            self.ui.warning("Could not load existing config cleanly; using safe defaults.")
            self.config = {}
        return True

    def _show_next_steps(self, existing_only: bool = False):
        """Display post-setup instructions."""
        self.ui.info("\nNext Steps:")
        self.ui.print(
            """
  ackstreet doctor
  ackstreet chat
  ackstreet run "your task here"
  ackstreet serve telegram
  ackstreet serve whatsapp
"""
        )
        if os.name == "nt":
            self.ui.info("Optional service setup: use Task Scheduler to run `ackstreet serve telegram` at logon.")
        else:
            self.ui.info("Optional service setup (Linux): create a systemd user unit for `ackstreet serve telegram`.")
        if existing_only:
            self.ui.info("No files were modified.")
