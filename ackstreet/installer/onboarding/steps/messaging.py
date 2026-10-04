"""Messaging gateway configuration step."""

from __future__ import annotations

from typing import Optional, Dict, Any
import json
import subprocess
import sys
from urllib import error, request

from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.validators import validate_telegram_token, validate_user_id
from ackstreet.installer.ui.config_writer import ConfigWriter


class MessagingSetupStep:
    """Configure Telegram/WhatsApp connectors."""

    def __init__(self, ui: Terminal, env_path, venv_python: Optional[str] = None):
        self.ui = ui
        self.env_path = env_path
        self.venv_python = venv_python or sys.executable

    def execute(
        self,
        non_interactive: bool = False,
        gateway: Optional[str] = None,
        telegram_token: str = "",
        telegram_user_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Interactively (or pre-seeded) set up a messaging connector."""
        selected = (gateway or "").strip().lower()
        if selected and selected not in {"telegram", "whatsapp", "none"}:
            self.ui.warning(f"Unknown gateway '{gateway}', skipping gateway setup.")
            return None

        if selected == "none":
            self.ui.info("Skipping messaging setup.")
            return None

        should_setup = bool(selected)
        if not should_setup:
            if non_interactive:
                self.ui.info("Non-interactive mode: skipping messaging setup.")
                return None
            should_setup = self.ui.confirm(
                "Do you want to set up a messaging gateway (Telegram/WhatsApp)?",
                default=False,
            )

        if not should_setup:
            self.ui.info("Skipping messaging setup. You can configure it later.")
            return None

        platform = selected
        if not platform:
            platforms = [
                ("Telegram (Long polling, works behind NAT, recommended)", "telegram"),
                ("WhatsApp (Multi-device, unofficial protocol, use at own risk)", "whatsapp"),
            ]
            platform = self.ui.menu("Which platform?", platforms)

        if platform == "telegram":
            return self._setup_telegram(
                non_interactive=non_interactive,
                token=telegram_token,
                user_id=telegram_user_id,
            )
        if platform == "whatsapp":
            return self._setup_whatsapp(non_interactive=non_interactive)
        return None

    def _setup_telegram(
        self,
        non_interactive: bool = False,
        token: str = "",
        user_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Interactive Telegram bot setup with verification."""
        self.ui.info("Setting up Telegram bot...")

        self.ui.instruction(
            [
                "Open Telegram and start a chat with @BotFather",
                "Send /newbot and follow the prompts",
                "Copy the token (format: 123456789:AA...)",
            ]
        )

        bot_token = (token or "").strip()
        if not bot_token:
            if non_interactive:
                self.ui.warning("Non-interactive mode without --telegram-token; skipping Telegram setup.")
                return None
            bot_token = self.ui.prompt(
                "Paste your bot token from @BotFather",
                is_secret=True,
                validator=lambda x: validate_telegram_token(x),
            )

        verified = False
        while True:
            self.ui.info("Verifying bot token with Telegram API...")
            verified = self._verify_telegram_token(bot_token)
            if verified:
                self.ui.success("Bot token verified!")
                break

            self.ui.error("Failed to verify token (invalid token or network timeout).")
            if non_interactive:
                self.ui.warning("Non-interactive mode: continuing without verification.")
                break
            action = self.ui.menu(
                "What do you want to do?",
                [
                    ("Retry verification", "retry"),
                    ("Enter a different token", "change"),
                    ("Skip verification for now", "skip"),
                ],
            )
            if action == "retry":
                continue
            if action == "change":
                bot_token = self.ui.prompt(
                    "Paste your bot token from @BotFather",
                    is_secret=True,
                    validator=lambda x: validate_telegram_token(x),
                )
                continue
            break

        ConfigWriter.write_env_file(
            self.env_path,
            {
                "ACKSTREET_TELEGRAM_BOT_TOKEN": bot_token,
            },
        )

        self.ui.warning(
            "IMPORTANT: An empty allowlist allows ANYONE to control this agent"
        )
        allowed_users = []

        if non_interactive:
            restrict = bool(user_id)
        else:
            restrict = self.ui.confirm(
                "Do you want to restrict access to only your account?", default=True
            )

        if restrict:
            self.ui.instruction(
                [
                    "Find your numeric Telegram user ID (for example via @userinfobot)",
                    "Paste the numeric ID below.",
                    "If you cannot do this now, finish setup and run:",
                    "ackstreet connect telegram --allow-user <YOUR_ID>",
                ]
            )
            user_id_str = str(user_id).strip()
            if user_id_str and not validate_user_id(user_id_str):
                self.ui.error("Invalid --telegram-user-id value, expected a numeric ID.")
                return None
            if not user_id_str:
                user_id_str = self.ui.prompt(
                    "Your Telegram User ID (numeric, e.g., 123456789)",
                    validator=lambda x: validate_user_id(x),
                )
            allowed_users = [int(user_id_str)]
            self.ui.success(f"Bot restricted to user ID: {user_id_str}")
        else:
            self.ui.warning("SECURITY WARNING: Bot is open to anyone who can message it.")

        if not verified:
            self.ui.warning("Telegram token was not verified during setup.")

        self.ui.info("Next command: ackstreet serve telegram")

        return {
            "enabled": True,
            "telegram": {
                "bot_token": "",
                "bot_token_env": "ACKSTREET_TELEGRAM_BOT_TOKEN",
                "allowed_user_ids": allowed_users,
                "allow_group_chats": False,
                "update_offset": 0,
            },
        }

    @staticmethod
    def _verify_telegram_token(token: str, timeout: float = 8.0) -> bool:
        """Verify Telegram bot token by calling getMe API."""
        if not validate_telegram_token(token):
            return False
        url = f"https://api.telegram.org/bot{token}/getMe"
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            return bool(data.get("ok") is True)
        except (error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return False

    def _setup_whatsapp(self, non_interactive: bool = False) -> Optional[Dict[str, Any]]:
        """Interactive WhatsApp setup."""
        self.ui.warning(
            "WhatsApp uses an UNOFFICIAL protocol (not endorsed by Meta)"
        )
        self.ui.info(
            "This can result in account bans. Telegram is recommended instead."
        )

        if non_interactive:
            self.ui.warning("Non-interactive mode: skipping WhatsApp QR login setup.")
            return None

        proceed = self.ui.confirm("Continue with WhatsApp?", default=False)
        if not proceed:
            self.ui.info("Skipping WhatsApp setup.")
            return None

        self.ui.info("Checking for WhatsApp dependencies...")
        # Check if neonize is installed
        try:
            import neonize  # noqa: F401

            self.ui.success("WhatsApp dependencies found")
        except ImportError:
            self.ui.warning(
                "WhatsApp requires optional dependencies."
            )
            install_now = self.ui.confirm("Install WhatsApp dependencies now?", default=True)
            if install_now:
                self.ui.info("Installing WhatsApp dependencies into the current virtualenv...")
                try:
                    subprocess.run(
                        [
                            self.venv_python,
                            "-m",
                            "pip",
                            "install",
                            "ackstreet-agent[whatsapp]",
                        ],
                        check=True,
                        capture_output=True,
                    )
                    self.ui.success("Dependencies installed")
                except subprocess.CalledProcessError:
                    self.ui.error("Failed to install dependencies")
                    return None
            else:
                return None

        self.ui.info("Next step: run `ackstreet connect whatsapp` to launch QR login.")
        return {
            "enabled": True,
            "whatsapp": {
                "session_path": "",
                "allowed_user_ids": [],
                "allow_group_chats": False,
            },
        }
