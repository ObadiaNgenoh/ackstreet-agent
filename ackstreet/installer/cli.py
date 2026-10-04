"""CLI entry point for the installer."""

from __future__ import annotations

import argparse
import platform
import sys

from ackstreet.installer.onboarding.orchestrator import (
    OnboardingOptions,
    OnboardingOrchestrator,
)


def _detect_os_name() -> str:
    system = platform.system().lower()
    release = platform.release().lower()
    if system == "windows":
        return "windows"
    if "microsoft" in release:
        return "wsl"
    if system == "darwin":
        return "darwin"
    return "linux_generic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ackstreet-install",
        description="Guided installer for ACKSTREET AGENT",
    )
    parser.add_argument("--os", dest="os_name", default=_detect_os_name())
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--provider", choices=["openrouter", "openai", "anthropic", "ollama", "custom"])
    parser.add_argument("--model")
    parser.add_argument("--api-key-env")
    parser.add_argument("--api-key")
    parser.add_argument("--gateway", choices=["telegram", "whatsapp", "none"])
    parser.add_argument("--telegram-token")
    parser.add_argument("--telegram-user-id")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--skip-doctor", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    opts = OnboardingOptions(
        os_name=args.os_name,
        non_interactive=args.non_interactive,
        yes=args.yes,
        skip_doctor=args.skip_doctor,
        provider=args.provider or "",
        model=args.model or "",
        api_key_env=args.api_key_env or "",
        api_key=args.api_key or "",
        gateway=args.gateway or "",
        telegram_token=args.telegram_token or "",
        telegram_user_id=args.telegram_user_id or "",
    )

    orchestrator = OnboardingOrchestrator(opts)
    return 0 if orchestrator.run() else 1


if __name__ == "__main__":
    sys.exit(main())
