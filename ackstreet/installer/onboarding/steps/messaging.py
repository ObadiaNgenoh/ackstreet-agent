"""Messaging gateway configuration step."""

from typing import Optional, Dict, Any
from ackstreet.installer.ui.terminal import Terminal
from ackstreet.installer.ui.validators import validate_telegram_token, validate_user_id


class MessagingSetupStep:
    """Configure Telegram/WhatsApp/Discord connectors."""

    def __init__(self, ui: Terminal):
        self.ui = ui

    def execute(self) -> Optional[Dict[str, Any]]:
        """
        Interactively set up messaging connector.

        Returns:
            Connector config dict or None if skipped
        """
        should_setup = self.ui.confirm(
            "Do you want to set up a messaging gateway (Telegram/WhatsApp/Discord)?",
            default=False,
        )

        if not should_setup:
            self.ui.info("Skipping messaging setup. You can configure it later.")
            return None

        platforms = [
            ("Telegram (Long polling, works behind NAT)", "telegram"),
            ("WhatsApp (Multi-device, unofficial protocol)", "whatsapp"),
            ("Discord (Webhook-based)", "discord"),
        ]

        platform = self.ui.menu("Which platform?", platforms)

        if platform == "telegram":
            return self._setup_telegram()
        elif platform == "whatsapp":
            return self._setup_whatsapp()
        elif platform == "discord":
            return self._setup_discord()

    def _setup_telegram(self) -> Dict[str, Any]:
        """Interactive Telegram bot setup."""
        self.ui.info("Setting up Telegram bot...")

        self.ui.instruction(
            [
                "Open Telegram and start a chat with @BotFather",
                "Send /newbot and follow the prompts",
                "BotFather will reply with a token like: 123456789:AAE...xyz",
            ]
        )

        bot_token = self.ui.prompt(
            "Paste your bot token from @BotFather",
            is_secret=True,
            validator=lambda x: validate_telegram_token(x),
        )

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
                    "Send this command to your bot: /whoami",
                    "Your bot will reply with your User ID",
                    "Paste that ID below to restrict access to only you",
                ]
            )

            user_id = self.ui.prompt(
                "Your Telegram User ID",
                validator=lambda x: validate_user_id(x),
            )
            allowed_users = [int(user_id)]
            self.ui.success(f"Bot restricted to user ID: {user_id}")
        else:
            self.ui.error("SECURITY WARNING: Bot is open to anyone who finds it!")

        return {
            "enabled": True,
            "telegram": {
                "bot_token": bot_token,
                "allowed_user_ids": allowed_users,
            },
        }

    def _setup_whatsapp(self) -> Optional[Dict[str, Any]]:
        """Interactive WhatsApp setup."""
        self.ui.warning(
            "WhatsApp uses an UNOFFICIAL protocol (not endorsed by Meta)"
        )
        self.ui.info(
            "This can result in account bans. Use Telegram if you depend on this."
        )

        proceed = self.ui.confirm("Continue with WhatsApp?", default=False)
        if not proceed:
            return None

        self.ui.info("WhatsApp setup will scan a QR code with your phone...")
        self.ui.instruction(
            [
                "When prompted, a QR code will appear in the terminal",
                "Open WhatsApp on your phone → Settings → Linked devices",
                "Scan the QR code",
                "Once linked, your login is saved automatically",
            ]
        )

        # The actual QR linking happens during first run
        return {
            "enabled": True,
            "whatsapp": {
                "session_path": "",  # Default: ~/.ackstreet/whatsapp/session.db
            },
        }

    def _setup_discord(self) -> Dict[str, Any]:
        """Interactive Discord bot setup."""
        self.ui.info("Setting up Discord bot...")

        self.ui.instruction(
            [
                "Go to Discord Developer Portal: https://discord.com/developers/applications",
                "Click 'New Application' and give it a name",
                "Go to the 'Bot' section and click 'Add Bot'",
                "Copy the token (keep it secret!)",
                "Under 'OAuth2 → URL Generator': select 'bot' and 'message content intent'",
                "Copy the generated URL and open it to invite the bot to your server",
            ]
        )

        bot_token = self.ui.prompt(
            "Paste your Discord bot token", is_secret=True
        )

        return {
            "enabled": True,
            "discord": {
                "bot_token": bot_token,
            },
        }
