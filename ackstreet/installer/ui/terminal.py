"""Interactive terminal UI with guided prompts."""

from typing import List, Optional, Callable, Any
import sys
import getpass


class Color:
    """ANSI color codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"


class Terminal:
    """Interactive terminal UI with guided prompts."""

    def banner(self, text: str):
        """Display a banner."""
        print(f"\n{Color.BOLD}{Color.CYAN}{text}{Color.RESET}\n")

    def section(self, text: str):
        """Display a section header."""
        print(f"\n{Color.BOLD}{Color.BLUE}→ {text}{Color.RESET}")

    def success(self, text: str):
        """Display a success message."""
        print(f"{Color.GREEN}✓{Color.RESET} {text}")

    def warning(self, text: str):
        """Display a warning."""
        print(f"{Color.YELLOW}⚠{Color.RESET} {text}")

    def error(self, text: str):
        """Display an error."""
        print(f"{Color.RED}✗{Color.RESET} {text}")

    def info(self, text: str):
        """Display info."""
        print(f"{Color.CYAN}ℹ{Color.RESET} {text}")

    def print(self, text: str):
        """Print plain text."""
        print(text)

    def menu(self, prompt: str, options: List[tuple]) -> Any:
        """
        Display a menu and return selected value.

        Args:
            prompt: Question to display
            options: List of (display_text, value) tuples

        Returns:
            The value of the selected option
        """
        print(f"\n{Color.BOLD}{prompt}{Color.RESET}")
        for i, (display, _) in enumerate(options, 1):
            print(f"  {i}. {display}")

        while True:
            try:
                choice = input(f"\nSelect (1-{len(options)}): ").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return options[idx][1]
                print(f"{Color.RED}Invalid choice{Color.RESET}")
            except (ValueError, KeyboardInterrupt):
                raise KeyboardInterrupt

    def prompt(
        self,
        text: str,
        default: Optional[str] = None,
        validator: Optional[Callable[[str], bool]] = None,
        is_secret: bool = False,
    ) -> str:
        """
        Interactive prompt with optional validation.

        Args:
            text: Question to ask
            default: Default value if empty
            validator: Function to validate input
            is_secret: Hide input (for passwords/tokens)

        Returns:
            User input
        """
        default_text = f" [{default}]" if default else ""
        prompt_text = f"{Color.BOLD}{text}{default_text}: {Color.RESET}"

        while True:
            try:
                if is_secret:
                    value = getpass.getpass(prompt_text)
                else:
                    value = input(prompt_text)

                if not value and default:
                    value = default

                if validator and not validator(value):
                    self.error("Invalid input, please try again")
                    continue

                return value
            except KeyboardInterrupt:
                raise

    def confirm(self, text: str, default: bool = False) -> bool:
        """Yes/no confirmation prompt."""
        default_str = "Y/n" if default else "y/N"
        response = input(f"{Color.BOLD}{text}? [{default_str}]: {Color.RESET}").lower()

        if not response:
            return default
        return response[0] == "y"

    def code_block(self, code: str, lang: str = ""):
        """Display code in a block."""
        print(f"\n{Color.DIM}```{lang}")
        print(code)
        print(f"```{Color.RESET}\n")

    def instruction(self, steps: List[str]):
        """Display step-by-step instructions."""
        for i, step in enumerate(steps, 1):
            print(f"  {Color.CYAN}{i}.{Color.RESET} {step}")
