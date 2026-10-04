"""Write configuration files safely."""

from typing import Dict, Any
from pathlib import Path

from ackstreet.config import _dump_toml, secure_private_file


class ConfigWriter:
    """Write TOML config files safely."""

    @staticmethod
    def write_config(path: Path, config: Dict[str, Any]):
        """Write configuration to TOML file using proper TOML format."""
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: Dict[str, Any] = {}
        if path.exists():
            try:
                try:
                    import tomllib  # py311+
                except ModuleNotFoundError:  # pragma: no cover - py<3.11
                    import tomli as tomllib  # type: ignore
                with open(path, "rb") as handle:
                    parsed = tomllib.load(handle)
                if isinstance(parsed, dict):
                    existing = parsed
            except Exception:
                existing = {}

        merged = ConfigWriter._deep_merge(existing, config)
        content = _dump_toml(merged)
        path.write_text(content, encoding="utf-8")
        secure_private_file(path)

    @staticmethod
    def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base)
        for key, value in (overlay or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = ConfigWriter._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

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
        secure_private_file(path)
