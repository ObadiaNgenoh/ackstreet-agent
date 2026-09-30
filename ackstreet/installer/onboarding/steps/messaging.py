"""Messaging gateway configuration step."""

from typing import Optional, Dict, Any
import subprocess
from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.validators import validate_telegram_token, validate_user_id


class MessagingSetupStep:
    """Configure Telegram/WhatsApp connectors."""

    def __init__(self, ui: Terminal):
        self.ui = ui

    def execute(self) -> Optional[Dict[str, Any]]:
        """
        Interactively set up messaging connector.

        Returns:
            Connector config dict or None if skipped
        """
        should_setup = self.ui.confirm(
            "Do you want to set up a messaging gateway (Telegram/WhatsApp)?",
            default=False,
        )

        if not should_setup:
            self.ui.info("Skipping messaging setup. You can configure it later.")
            return None

        platforms = [
            ("Telegram (Long polling, works behind NAT, recommended)", "telegram"),
            ("WhatsApp (Multi-device, unofficial protocol, use at own risk)", "whatsapp"),
        ]

        platform = self.ui.menu("Which platform?", platforms)

        if platform == "telegram":
            return self._setup_telegram()
        elif platform == "whatsapp":
            return self._setup_whatsapp()

    def _setup_telegram(self) -> Optional[Dict[str, Any]]:
        """Interactive Telegram bot setup with verification."""
        self.ui.info("Setting up Telegram bot...")

        self.ui.instruction(
            [
                "Open Telegram and start a chat with @BotFather",
                "Send /newbot and follow the prompts",
                "Choose a display name for your bot",
                "Choose a username ending in 'bot' (e.g., my_ackstreet_bot)",
                "BotFather will reply with a token like: 123456789:AAE...xyz",
            ]
        )

        bot_token = self.ui.prompt(
            "Paste your bot token from @BotFather",
            is_secret=True,
            validator=lambda x: validate_telegram_token(x),
        )

        # Verify the token by calling getMe
        self.ui.info("Verifying bot token with Telegram API...")
        if not self._verify_telegram_token(bot_token):
            self.ui.error("Failed to verify token. Please check and try again.")
            retry = self.ui.confirm("Try again?", default=True)
            if retry:
                return self._setup_telegram()
            return None

        self.ui.success("Bot token verified!")

        # Security: Ask for allowlist
        self.ui.warning(
            "IMPORTANT: An empty allowlist allows ANYONE to control this agent"
        )

        setup_allowlist = self.ui.confirm(
            "Do you want to restrict access to only your account?", default=True
        )

        allowed_users = []
        if setup_allowlist:
            self.ui.instruction(
                [
                    "Option 1 (Recommended): Enter your User ID directly (numeric)",
                    "Option 2: Start the bot, send /whoami to it, and paste the ID it replies with",
                ]
            )

            user_id_str = self.ui.prompt(
                "Your Telegram User ID (numeric, e.g., 123456789)",
                validator=lambda x: validate_user_id(x),
            )
            allowed_users = [int(user_id_str)]
            self.ui.success(f"Bot restricted to user ID: {user_id_str}")
        else:
            self.ui.error("SECURITY WARNING: Bot is open to anyone who finds it!")

        return {
            "enabled": True,
            "telegram": {
                "bot_token": bot_token,
                "allowed_user_ids": allowed_users,
                "allow_group_chats": False,
                "update_offset": 0,
            },
        }

    @staticmethod
    def _verify_telegram_token(token: str) -> bool:
        """Verify Telegram bot token by calling getMe API."""
        try:
            import httpx

            client = httpx.Client(timeout=5.0)
            response = client.get(
                f"https://api.telegram.org/bot{token}/getMe",
                follow_redirects=True,
            )
            client.close()

            if response.status_code == 200:
                data = response.json()
                return data.get("ok") is True
            return False
        except Exception:
            return False

    def _setup_whatsapp(self) -> Optional[Dict[str, Any]]:
        """Interactive WhatsApp setup."""
        self.ui.warning(
            "WhatsApp uses an UNOFFICIAL protocol (not endorsed by Meta)"
        )
        self.ui.info(
            "This can result in account bans. Telegram is recommended instead."
        )

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
            self.ui.error(
                "WhatsApp requires: pip install 'ackstreet-agent[whatsapp]'"
            )
            install_now = self.ui.confirm("Install WhatsApp dependencies now?", default=True)
            if install_now:
                self.ui.info("Installing WhatsApp dependencies...")
                try:
                    import subprocess

                    subprocess.run(
                        [
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

        self.ui.info("WhatsApp setup will scan a QR code with your phone...")
        self.ui.instruction(
            [
                "When the QR code appears in the terminal, scan it with your phone",
                "Open WhatsApp on your phone → Settings → Linked devices",
                "Scan the QR code that appears below",
                "Once linked, your login is saved automatically",
            ]
        )

        # The actual QR linking happens during first run
        return {
            "enabled": True,
            "whatsapp": {
                "session_path": "",  # Default: ~/.ackstreet/whatsapp/session.db
                "allowed_user_ids": [],
                "allow_group_chats": False,
            },
        }
