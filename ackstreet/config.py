"""Configuration for ACKSTREET AGENT.

Resolution order (highest priority first):

1. Environment variables named ``ACKSTREET_<SECTION>_<KEY>``
   e.g. ``ACKSTREET_AGENT_PROVIDER=ollama``, ``ACKSTREET_TOOLS_ALLOW_SHELL=false``
2. The TOML config file: ``$ACKSTREET_CONFIG`` or ``~/.ackstreet/config.toml``
3. Built-in defaults (:data:`DEFAULTS`)

The config file is intentionally plain TOML so it can be read, diffed and
hand-edited without tooling.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

try:  # Python 3.11+
    import tomllib as _toml_reader
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as _toml_reader  # type: ignore


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def home_dir() -> Path:
    """Root directory for all ACKSTREET AGENT state."""
    override = os.environ.get("ACKSTREET_HOME")
    if override:
        return Path(override).expanduser()
    return Path("~/.ackstreet").expanduser()


def config_path() -> Path:
    override = os.environ.get("ACKSTREET_CONFIG")
    if override:
        return Path(override).expanduser()
    return home_dir() / "config.toml"


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """\
You are {agent_name}, a self-hosted autonomous agent running on the user's own machine.

You work by taking actions, not by describing them. You have tools for shell
execution, reading and writing files, fetching web pages and executing Python.

Operating rules:
1. Break the task into steps and execute them one at a time with tools.
2. Verify your work: after writing a file, read it back; after a command, check output.
3. If a tool fails, read the error and adjust. Do not repeat an identical failing call.
4. When you learn a reusable procedure, save it as a skill with `save_skill` so you
   can reuse it in later sessions.
5. Never invent file contents, command output, or facts. If you do not know, use a tool.
6. Finish with a short report of what you actually did and what remains undone.
"""

DEFAULTS: Dict[str, Any] = {
    "agent": {
        "name": "Ackstreet",
        # Which entry under [providers] to use.
        "provider": "openai",
        "model": "",                # empty = inherit the provider's model
        "max_steps": 25,
        "temperature": 0.2,
        "system_prompt": "",        # empty = DEFAULT_SYSTEM_PROMPT
        "context_messages": 40,     # how many past messages to send to the model
        "auto_curate": True,        # propose a skill after a finished session
        "auto_load_skill": True,    # inject the skills index into the system prompt
        # Approval gate for dangerous tools (shell, write_file, edit_file,
        # delete_file, python, save_skill, update_skill).
        #   "auto"      run them without asking (default; `doctor` warns)
        #   "ask"       ask a human before every dangerous call
        #   "allowlist" run calls matching approval_allowlist, ask for the rest
        "approval_mode": "auto",
        "approval_allowlist": [],   # e.g. ["ls", "git status*", "read_file:docs/*"]
        "approval_denylist": [],    # refused in EVERY mode, even "auto"
    },
    "providers": {
        "openai": {
            "type": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
            "model": "gpt-4o-mini",
        },
        "anthropic": {
            "type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-sonnet-4-5",
        },
        "ollama": {
            "type": "ollama",
            "base_url": "http://localhost:11434",
            "api_key_env": "",
            "model": "llama3.1",
        },
        "custom": {
            "type": "openai",
            "base_url": "http://localhost:8000/v1",
            "api_key_env": "CUSTOM_API_KEY",
            "model": "local-model",
        },
    },
    "tools": {
        "allow_shell": True,
        "allow_web": True,
        "allow_python": True,
        "shell_timeout": 60,
        "web_timeout": 30,
        "max_output_chars": 20000,
        "blocked_commands": [
            "rm -rf /",
            "mkfs",
            ":(){:|:&};:",
            "dd if=/dev/zero of=/dev/",
            "shutdown",
            "reboot",
        ],
    },
    "memory": {
        "enabled": True,
        "recall_limit": 8,
        "max_sessions": 500,
    },
    "skills": {
        "enabled": True,
        "min_steps_to_curate": 3,
    },
    "connectors": {
        # Chat-platform bridges. Each connector only moves text in and out of
        # the same agent loop the CLI uses, so the approval gate still applies.
        "enabled": True,
        # SECURITY: empty means ANY user who can reach the bot may drive the
        # agent on this machine. Set explicit ids, or "*" to allow everyone
        # on purpose. `ackstreet doctor` warns while this is empty.
        "allowed_user_ids": [],
        "allow_group_chats": False,
        # How long a chat keeps its conversation context while idle (seconds).
        "session_ttl": 3600,
        # How long to wait for a yes/no answer to an approval prompt.
        # A timeout is always a refusal.
        "approval_timeout": 300,
        "telegram": {
            "bot_token": "",
            "allowed_user_ids": [],
            "allow_group_chats": False,
            "update_offset": 0,
        },
        "whatsapp": {
            # Empty = <ACKSTREET home>/whatsapp/session.db
            "session_path": "",
            "allowed_user_ids": [],
            "allow_group_chats": False,
        },
    },
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge *overlay* into a copy of *base*."""
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce(value: str) -> Any:
    """Turn an environment-variable string into a typed value."""
    low = value.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", ""):
        return ""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


#: Environment variables that override a provider's ``base_url``, in priority
#: order. These let a container, CI job or VM point a provider at a different
#: endpoint (a proxy, a mock server, a self-hosted gateway) without editing
#: ``config.toml``.
PROVIDER_BASE_URL_ENVS: Dict[str, tuple] = {
    "openai": ("OPENAI_BASE_URL", "OPENAI_API_BASE"),
    "anthropic": ("ANTHROPIC_BASE_URL",),
    "ollama": ("OLLAMA_HOST",),
    "custom": ("CUSTOM_BASE_URL",),
}

#: Environment variables that override a provider's ``model``.
PROVIDER_MODEL_ENVS: Dict[str, tuple] = {
    "openai": ("OPENAI_MODEL",),
    "anthropic": ("ANTHROPIC_MODEL",),
    "ollama": ("OLLAMA_MODEL",),
    "custom": ("CUSTOM_MODEL",),
}


def _apply_env_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Apply ``ACKSTREET_<SECTION>_<KEY>`` environment overrides."""
    prefix = "ACKSTREET_"
    skip = {"ACKSTREET_CONFIG", "ACKSTREET_HOME"}
    for env_key, raw in os.environ.items():
        if not env_key.startswith(prefix) or env_key in skip:
            continue
        path = env_key[len(prefix):].lower().split("_")
        if len(path) < 2:
            continue
        # Walk the config tree, greedily matching the longest section name.
        section = path[0]
        rest = path[1:]
        if section in cfg and isinstance(cfg[section], dict):
            key = "_".join(rest)
            if key in cfg[section]:
                cfg[section][key] = _coerce(raw)
    return cfg


def _dump_toml(data: Dict[str, Any]) -> str:
    """Minimal TOML writer covering the shapes this config uses."""
    lines: list[str] = []

    def fmt(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(fmt(v) for v in value) + "]"
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def key(name: str) -> str:
        # A bare TOML key cannot contain a dot; quoting keeps such a key literal
        # instead of silently becoming a nested table.
        text = str(name)
        if any(ch in text for ch in ". \t\"") or text == "":
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        return text

    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for name, value in scalars.items():
        lines.append(f"{key(name)} = {fmt(value)}")

    for name, section in data.items():
        if not isinstance(section, dict):
            continue
        lines.append("")
        lines.append(f"[{key(name)}]")
        for sub_name, sub_value in section.items():
            if isinstance(sub_value, dict):
                continue
            lines.append(f"{key(sub_name)} = {fmt(sub_value)}")
        for sub_name, sub_value in section.items():
            if isinstance(sub_value, dict):
                lines.append("")
                lines.append(f"[{key(name)}.{key(sub_name)}]")
                for leaf_name, leaf_value in sub_value.items():
                    if isinstance(leaf_value, dict):
                        lines.append("")
                        lines.append(f"[{key(name)}.{key(sub_name)}.{key(leaf_name)}]")
                        for deep_name, deep_value in leaf_value.items():
                            if not isinstance(deep_value, dict):
                                lines.append(f"{key(deep_name)} = {fmt(deep_value)}")
                        continue
                    lines.append(f"{key(leaf_name)} = {fmt(leaf_value)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Provider descriptor
# --------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """Resolved settings for one LLM backend."""

    name: str
    type: str
    base_url: str
    model: str
    api_key: str = ""
    api_key_env: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_key(self) -> bool:
        """True when the provider does not need a key, or has one."""
        return bool(self.api_key) or self.type == "ollama"


# --------------------------------------------------------------------------
# Config object
# --------------------------------------------------------------------------

class Config:
    """Loaded, merged configuration."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self.data = data
        self.path = path or config_path()

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Config:
        """Load defaults, layer the TOML file, then environment overrides."""
        target = Path(path).expanduser() if path else config_path()
        data = copy.deepcopy(DEFAULTS)

        if target.exists():
            with open(target, "rb") as handle:
                file_data = _toml_reader.load(handle)
            data = _deep_merge(data, file_data)

        data = _apply_env_overrides(data)
        return cls(data, target)

    def to_toml(self) -> str:
        return _dump_toml(self.data)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.to_toml(), encoding="utf-8")
        return self.path

    # -- accessors ---------------------------------------------------------

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        """Set a value, creating intermediate tables for dotted keys.

        ``set("providers", "openai.model", "gpt-4o")`` nests properly into
        ``[providers.openai]``. Without this, a dotted key would be stored as a
        literal flat key and the TOML writer would then emit a duplicate table
        header, making the config file unparseable.
        """
        target = self.data.setdefault(section, {})
        parts = [p for p in str(key).split(".") if p]
        if not parts:
            raise KeyError("config key must not be empty")
        for part in parts[:-1]:
            nxt = target.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                target[part] = nxt
            target = nxt
        target[parts[-1]] = value

    # -- paths -------------------------------------------------------------

    @property
    def root(self) -> Path:
        return home_dir()

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def ensure_dirs(self) -> None:
        for directory in (
            self.root,
            self.skills_dir,
            self.memory_dir,
            self.sessions_dir,
            self.workspace,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # -- providers ---------------------------------------------------------

    def provider_names(self) -> list[str]:
        return sorted(self.data.get("providers", {}).keys())

    def resolve_provider(self, name: Optional[str] = None) -> ProviderConfig:
        """Resolve the active provider into a :class:`ProviderConfig`.

        The provider's model can be overridden by ``agent.model``.
        """
        providers = self.data.get("providers", {})
        chosen = name or self.get("agent", "provider", "openai")

        if chosen not in providers:
            available = ", ".join(sorted(providers)) or "(none configured)"
            raise KeyError(
                f"Provider '{chosen}' is not defined in the config. Available: {available}"
            )

        raw = providers[chosen]
        api_key = ""
        key_env = raw.get("api_key_env") or ""
        if key_env:
            api_key = os.environ.get(key_env, "")

        base_url = (raw.get("base_url") or "").rstrip("/")
        for env_name in PROVIDER_BASE_URL_ENVS.get(chosen, ()):
            if os.environ.get(env_name):
                base_url = os.environ[env_name].rstrip("/")
                break

        # Precedence: provider-specific env var > agent.model > provider model.
        model = self.get("agent", "model", "") or raw.get("model", "")
        for env_name in PROVIDER_MODEL_ENVS.get(chosen, ()):
            if os.environ.get(env_name):
                model = os.environ[env_name]
                break

        extra = {
            k: v for k, v in raw.items()
            if k not in {"type", "base_url", "api_key_env", "model"}
        }

        return ProviderConfig(
            name=chosen,
            type=raw.get("type", "openai"),
            base_url=base_url,
            model=model,
            api_key=api_key,
            api_key_env=key_env,
            extra=extra,
        )

    # -- prompt ------------------------------------------------------------

    def system_prompt(self) -> str:
        custom = self.get("agent", "system_prompt", "")
        if custom:
            return custom
        return DEFAULT_SYSTEM_PROMPT.format(
            agent_name=self.get("agent", "name", "Ackstreet")
        )


    # -- safety ------------------------------------------------------------

    def approval_policy(self, approver=None):
        """Build the approval gate for dangerous tools.

        Imported lazily because :mod:`ackstreet.safety` imports this module.
        """
        from .safety import ApprovalPolicy

        return ApprovalPolicy(self, approver=approver)


def load_config(path: Optional[Path] = None) -> Config:
    """Convenience wrapper around :meth:`Config.load`."""
    return Config.load(path)
