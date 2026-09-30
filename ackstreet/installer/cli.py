"""CLI entry point for the installer."""

import sys
from ackstreet.installer.onboarding.orchestrator import OnboardingOrchestrator


def main():
    """Entry point for the installer."""
    os_name = sys.argv[1] if len(sys.argv) > 1 else "linux_generic"
    orchestrator = OnboardingOrchestrator(os_name)

    success = orchestrator.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
