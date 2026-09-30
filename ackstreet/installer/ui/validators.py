"""Input validators for the installer."""

import re


def validate_openai_key(key: str) -> bool:
    """Validate OpenAI API key format."""
    return key.startswith("sk-") and len(key) > 20


def validate_anthropic_key(key: str) -> bool:
    """Validate Anthropic API key format."""
    return key.startswith("sk-ant-") and len(key) > 30


def validate_telegram_token(token: str) -> bool:
    """Validate Telegram bot token format."""
    # Format: digits:alphanumeric
    return bool(re.match(r"^\d+:[a-zA-Z0-9_-]{25,}$", token))


def validate_user_id(user_id: str) -> bool:
    """Validate user ID (numeric)."""
    try:
        int(user_id)
        return True
    except ValueError:
        return False


def validate_url(url: str) -> bool:
    """Validate URL format."""
    return url.startswith(("http://", "https://"))
