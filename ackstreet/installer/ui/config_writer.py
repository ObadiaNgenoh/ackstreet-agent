"""Write configuration files safely."""

from typing import Dict, Any
from pathlib import Path
import json


class ConfigWriter:
    """Write TOML config files safely."""

    @staticmethod
    def write_config(path: Path, config: Dict[str, Any]):
        """Write configuration to TOML file using proper TOML format."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create backup if exists
        if path.exists():
            backup = path.with_suffix(".toml.backup")
            if backup.exists():
                backup.unlink()
            path.rename(backup)

        # Use proper TOML serialization
        content = ConfigWriter._dict_to_toml(config)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)  # Restrict permissions

    @staticmethod
    def _escape_toml_string(value: str) -> str:
        """Escape a string for TOML."""
        # TOML requires double-quoted strings with proper escaping
        escaped = value.replace("\\", "\\\\").replace('"', "\\\"")
        return f'"{escaped}"'

    @staticmethod
    def _dict_to_toml(d: Dict[str, Any], parent_key: str = "") -> str:
        """Convert dict to TOML format with proper syntax."""
        lines = []
        scalars = {}
        tables = {}
        array_tables = {}

        # Separate scalars from tables
        for key, value in d.items():
            if isinstance(value, dict):
                tables[key] = value
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                array_tables[key] = value
            else:
                scalars[key] = value

        # Write scalars first
        for key, value in scalars.items():
            lines.append(ConfigWriter._format_key_value(key, value))

        # Write regular tables
        for key, table in tables.items():
            if lines and not lines[-1].startswith("["):
                lines.append("")  # Blank line before section
            table_name = f"{parent_key}.{key}" if parent_key else key
            lines.append(f"[{table_name}]")
            for sub_key, sub_value in table.items():
                if not isinstance(sub_value, (dict, list)):
                    lines.append(ConfigWriter._format_key_value(sub_key, sub_value))
            # Handle nested dicts
            for sub_key, sub_value in table.items():
                if isinstance(sub_value, dict):
                    nested_name = f"{table_name}.{sub_key}"
                    lines.append(f"\n[{nested_name}]")
                    for nested_key, nested_val in sub_value.items():
                        lines.append(ConfigWriter._format_key_value(nested_key, nested_val))

        # Write array of tables
        for key, array in array_tables.items():
            if lines:
                lines.append("")  # Blank line before array
            for item in array:
                array_name = f"{parent_key}.{key}" if parent_key else key
                lines.append(f"[[{array_name}]]")
                for item_key, item_value in item.items():
                    lines.append(ConfigWriter._format_key_value(item_key, item_value))

        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_key_value(key: str, value: Any) -> str:
        """Format a single key-value pair for TOML."""
        # Quote key if it contains special characters
        safe_key = key
        if any(ch in key for ch in [" ", ".", "-", '"']):
            safe_key = f'"{key}"'

        if isinstance(value, bool):
            return f"{safe_key} = {str(value).lower()}"
        elif isinstance(value, (int, float)):
            return f"{safe_key} = {value}"
        elif isinstance(value, list):
            # Format array
            formatted_items = []
            for item in value:
                if isinstance(item, bool):
                    formatted_items.append(str(item).lower())
                elif isinstance(item, (int, float)):
                    formatted_items.append(str(item))
                elif isinstance(item, str):
                    # Escape string items
                    escaped = item.replace("\\", "\\\\").replace('"', "\\\"")
                    formatted_items.append(f'"{escaped}"')
                else:
                    formatted_items.append(str(item))
            return f"{safe_key} = [{', '.join(formatted_items)}]"
        else:
            # String value - must use double quotes in TOML
            return f"{safe_key} = {ConfigWriter._escape_toml_string(str(value))}"

    @staticmethod
    def write_env_file(path: Path, env_vars: Dict[str, str]):
        """Write .env file with API keys."""
        path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing
        existing = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

        # Update with new values
        existing.update(env_vars)

        # Write back in order
        lines = []
        lines.append("# ACKSTREET AGENT — API Keys and Secrets")
        lines.append("# This file is sourced by the installer and must be kept secure.")
        lines.append("# Do not commit this file to version control.")
        lines.append("")
        
        for k, v in existing.items():
            # Quote values that contain spaces or special characters
            if " " in v or "\t" in v:
                lines.append(f"{k}='{v}'")
            else:
                lines.append(f"{k}={v}")

        content = "\n".join(lines)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)  # Restrict permissions
