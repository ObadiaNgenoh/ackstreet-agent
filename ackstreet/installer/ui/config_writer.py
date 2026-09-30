"""Write configuration files safely."""

from typing import Dict, Any
from pathlib import Path


class ConfigWriter:
    """Write TOML config files safely."""

    @staticmethod
    def write_config(path: Path, config: Dict[str, Any]):
        """Write configuration to TOML file."""
        try:
            import tomllib  # Python 3.11+
            import tomli_w  # We'll use a simpler approach
        except ImportError:
            pass  # Fall back to manual TOML writing

        path.parent.mkdir(parents=True, exist_ok=True)

        # Create backup if exists
        if path.exists():
            backup = path.with_suffix(".toml.backup")
            path.rename(backup)

        # Simple TOML writer (for basic config)
        content = ConfigWriter._dict_to_toml(config)
        path.write_text(content)
        path.chmod(0o600)  # Restrict permissions

    @staticmethod
    def _dict_to_toml(d: Dict[str, Any], indent: int = 0) -> str:
        """Convert dict to TOML format."""
        lines = []
        indent_str = "  " * indent

        for key, value in d.items():
            if isinstance(value, dict):
                lines.append(f"\n{indent_str}[{key}]")
                lines.append(ConfigWriter._dict_to_toml(value, indent + 1))
            elif isinstance(value, list):
                if value and isinstance(value[0], dict):
                    # Array of tables
                    for item in value:
                        lines.append(f"\n{indent_str}[[{key}]]")
                        lines.append(ConfigWriter._dict_to_toml(item, indent + 1))
                else:
                    # Regular array
                    array_str = "[" + ", ".join(repr(v) for v in value) + "]"
                    lines.append(f"{indent_str}{key} = {array_str}")
            elif isinstance(value, bool):
                lines.append(f"{indent_str}{key} = {str(value).lower()}")
            elif isinstance(value, (int, float)):
                lines.append(f"{indent_str}{key} = {value}")
            else:
                lines.append(f"{indent_str}{key} = {repr(value)}")

        return "\n".join(lines)

    @staticmethod
    def write_env_file(path: Path, env_vars: Dict[str, str]):
        """Write .env file with API keys."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing
        existing = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

        # Update
        existing.update(env_vars)

        # Write back
        content = "\n".join(f"{k}={v}" for k, v in existing.items())
        path.write_text(content)
        path.chmod(0o600)  # Restrict permissions
