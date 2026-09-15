"""CLI implementations for `ackstreet connect`, `serve` and `connectors`.

These live beside the connectors rather than inside ``cli.py`` on purpose: the
agent core does not need to know which platforms exist. ``cli.py`` only wires
the argument parsers to the three entry points below.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from typing import Any, Dict, List

from ..config import Config
from . import registry
from .base import Connector, ConnectorError, NotInstalledError
from .router import MessageRouter


def _cli():
    """Import the CLI helpers lazily (cli imports this module)."""
    from .. import cli

    return cli


def _load(args: argparse.Namespace) -> Config:
    return _cli().load(args)


def _resolve(name: str) -> type:
    """Look up a connector, importing the built-ins on first use."""
    registry.load_builtin()
    return registry.get(name)


def _available() -> List[str]:
    registry.load_builtin()
    return registry.names()


def _warn_if_open(cfg: Config, connector: Connector) -> None:
    """Print the security warning when the allowlist is empty."""
    cli = _cli()
    allowed = connector.authorized_user_ids()
    if allowed:
        print()
        print(f"Allowlist: {cli.bold(', '.join(allowed))}")
        print(cli.dim("  Only these user ids can drive the agent from this platform."))
        print()
        return
    print()
    print(cli.yellow(cli.bold("  SECURITY WARNING")))
    print(
        "  The user allowlist is empty, so ANYONE who can message this bot can run"
    )
    print(
        "  the agent on this machine -- including shell commands and file writes."
    )
    print("  Restrict it before exposing the bot:")
    print(f"    ackstreet connect {connector.name} --allow-user <id>")
    print(f"  or set connectors.{connector.name}.allowed_user_ids in the config.")
    print(
        "  Use /whoami in the chat to learn your own id, or '*' to allow everyone"
    )
    print("  deliberately.")
    print()


# --------------------------------------------------------------------------
# connect
# --------------------------------------------------------------------------

def cmd_connect(args: argparse.Namespace) -> int:
    """Configure a connector's credentials and allowlist."""
    cli = _cli()
    cfg = _load(args)
    cfg.ensure_dirs()

    if not getattr(args, "platform", None):
        print(cli.bold("Connect a chat platform\n"))
        for name in _available():
            cls = _resolve(name)
            state = "configured" if cls.is_configured(cfg) else "not configured"
            print(f"  {name:<10} {cls.display_name:<10} [{state}]")
            print(f"             install:  pip install 'ackstreet-agent[{cls.extra}]'")
            print(f"             connect:  ackstreet connect {name}")
        return 0

    name = args.platform
    try:
        cls = _resolve(name)
    except ConnectorError as exc:
        print(cli.red(str(exc)))
        return 2

    # -- Telegram ---------------------------------------------------------
    if name == "telegram":
        token = (getattr(args, "token", None) or "").strip()
        if not token:
            existing = cls.settings(cfg).get("bot_token", "")
            if existing:
                print(f"Telegram already has a token configured ({_mask(existing)}).")
                print("Pass --token to replace it.")
            else:
                print(cli.bold("Telegram setup\n"))
                print("1. Open Telegram and start a chat with @BotFather.")
                print("2. Send /newbot and follow the prompts (name, then username).")
                print("3. BotFather replies with a token like 123456789:AAE...xyz")
                print("4. Run:  ackstreet connect telegram --token <token>")
            return 0

        cls.store_credentials(cfg, token=token)
        print(cli.green(f"Saved the Telegram bot token to {cfg.path}"))

        if not getattr(args, "no_verify", False):
            connector = cls(cfg)
            try:
                me = connector.get_me()
                username = me.get("username", "?")
                print(cli.green(f"Token verified: bot is @{username}"))
                print("  Send it a message after starting the listener below.")
            except ConnectorError as exc:
                print(cli.red(f"Token verification failed: {exc}"))
                return 3
            finally:
                connector.close()

    # -- WhatsApp ---------------------------------------------------------
    elif name == "whatsapp":
        session_path = (getattr(args, "session_path", None) or "").strip()
        if session_path:
            cls.store_credentials(cfg, session_path=session_path)
            print(cli.green(f"Session path set to {session_path}"))

        connector = cls(cfg)
        print(cli.bold("WhatsApp setup\n"))
        print("WhatsApp has no bot API, so this uses the WhatsApp Web multi-device")
        print("protocol (the same mechanism as WhatsApp Web / Desktop).")
        print(cli.yellow("  Unofficial protocol: use at your own risk."))
        print()
        connector.print_setup_instructions()
        print(f"  Session file: {connector.session_path}")

        try:
            connector._require_neonize()
        except NotInstalledError as exc:
            print()
            print(cli.yellow(str(exc)))
            print()
            print("The connector code and its tests are complete; only the QR scan")
            print("itself needs the library installed.")
            return 4

        print("Starting the login flow; scan the QR code that appears below.")
        print(cli.dim("(press Ctrl-C to abort)"))
        try:
            _run_whatsapp_login(connector, timeout=getattr(args, "timeout", 120))
        except KeyboardInterrupt:
            print()
            print(cli.yellow("aborted"))
            return 130
        except ConnectorError as exc:
            print(cli.red(str(exc)))
            return 3
        return 0

    else:
        print(cli.red(f"no setup flow for '{name}'"))
        return 2

    # -- shared: allowlist -------------------------------------------------
    allow = list(getattr(args, "allow_user", None) or [])
    if allow:
        settings = dict(cls.settings(cfg))
        existing = [str(v) for v in settings.get("allowed_user_ids", []) or []]
        for value in allow:
            value = str(value).strip()
            if value and value not in existing:
                existing.append(value)
        settings["allowed_user_ids"] = existing
        cfg.set("connectors", name, settings)
        cfg.save()
        print(cli.green(f"Allowlist updated: {', '.join(existing)}"))

    connector = cls(cfg)
    _warn_if_open(cfg, connector)
    print(f"Next:  {cli.bold(f'ackstreet serve {name}')}")
    return 0


def _mask(value: str) -> str:
    value = str(value)
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def _run_whatsapp_login(connector, timeout: float = 120) -> None:
    """Drive the QR login once and return when the device is linked."""
    client = connector.client
    types = connector._resolve_event_types()

    def on_qr(_client: Any, payload: Any = None, *_args: Any) -> None:
        connector.handle_qr(payload)

    def on_connected(_client: Any, *_args: Any) -> None:
        connector.connected.set()

    decorator = getattr(client, "event", None)
    if callable(decorator):
        if "qr" in types:
            decorator(types["qr"])(on_qr)
        if "connected" in types:
            decorator(types["connected"])(on_connected)

    connector.session_path.parent.mkdir(parents=True, exist_ok=True)
    client.connect()

    if connector.connected.wait(timeout):
        print()
        print("WhatsApp linked. The session is saved and will survive a restart.")
        print("Next:  ackstreet serve whatsapp")
    else:
        print()
        print("Still waiting for the scan. Re-run `ackstreet connect whatsapp` if the")
        print("code expired.")


# --------------------------------------------------------------------------
# serve
# --------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    """Run a connector's listener, routing messages through the agent."""
    cli = _cli()
    cfg = _load(args)
    cfg.ensure_dirs()

    if not cfg.get("connectors", "enabled", True):
        print(cli.red("connectors are disabled (connectors.enabled = false)"))
        return 2

    try:
        cls = _resolve(args.platform)
    except ConnectorError as exc:
        print(cli.red(str(exc)))
        return 2

    if args.yes:
        cfg.set("agent", "approval_mode", "auto")

    connector = cls(cfg)

    if not cls.is_configured(cfg):
        print(cli.red(f"{args.platform} is not configured yet."))
        print(f"Run:  ackstreet connect {args.platform}")
        return 2

    cli.print_banner()
    spec = cfg.resolve_provider()
    print(f"connector  {cli.bold(connector.display_name)}")
    print(f"provider   {cli.bold(spec.name)}  model {cli.bold(spec.model or '(unset)')}")
    print(f"approvals  {cli.bold(_approval_summary(cfg, args))}")
    print(f"sessions   {cli.bold(str(cfg.get('connectors', 'session_ttl', 3600)))}s idle TTL")

    allowed = connector.authorized_user_ids()
    if allowed:
        print(f"allowlist  {cli.bold(', '.join(allowed))}")
    else:
        print(f"allowlist  {cli.red('EMPTY -- anyone who can message the bot can drive this agent')}")

    router = MessageRouter(cfg, connector, log=_make_logger(args))
    print()
    print(cli.dim("listening for messages... (Ctrl-C to stop)"))
    print()

    stop_event = threading.Event()
    previous = signal.getsignal(signal.SIGINT)

    def _on_signal(signum, frame):  # noqa: ARG001
        print()
        print(cli.yellow("stopping..."))
        stop_event.set()
        connector.stop()

    try:
        signal.signal(signal.SIGINT, _on_signal)
    except ValueError:  # not the main thread (e.g. under a test runner)
        previous = None

    try:
        connector.listen(on_message=router.dispatch_async, stop_event=stop_event)
    except NotInstalledError as exc:
        print(cli.yellow(str(exc)))
        return 4
    except ConnectorError as exc:
        print(cli.red(f"connector error: {exc}"))
        return 3
    except KeyboardInterrupt:
        pass
    finally:
        # Let an in-flight turn finish before tearing the connector down.
        router.wait_for_idle(timeout=30.0)
        router.shutdown()
        connector.close()
        if previous is not None:
            try:
                signal.signal(signal.SIGINT, previous)
            except ValueError:
                pass

    print()
    print(cli.dim(router.describe()))
    print(cli.dim("bye"))
    return 0


def _approval_summary(cfg: Config, args: argparse.Namespace) -> str:
    mode = "auto (locked open by --yes)" if args.yes else str(
        cfg.get("agent", "approval_mode", "auto")
    )
    if mode.startswith("ask") or mode.startswith("allowlist"):
        return f"{mode}, asked in-chat (timeout {cfg.get('connectors', 'approval_timeout', 300)}s)"
    return mode


def _make_logger(args: argparse.Namespace):
    cli = _cli()
    quiet = bool(getattr(args, "quiet", False))

    def log(message: str) -> None:
        if quiet:
            return
        print(cli.dim(f"  [connector] {message}"), file=sys.stderr)

    return log


# --------------------------------------------------------------------------
# connectors (status)
# --------------------------------------------------------------------------

def cmd_connectors(args: argparse.Namespace) -> int:
    """List every connector and whether it is ready to run."""
    cli = _cli()
    cfg = _load(args)

    registry.load_builtin()
    names = registry.names()
    if not names:
        print(cli.yellow("no connectors are registered"))
        return 1

    print(cli.bold(f"{len(names)} connector(s) available\n"))
    for name in names:
        cls = registry.get(name)
        connector = cls(cfg)
        configured = cls.is_configured(cfg)
        mark = cli.green("ready ") if configured else cli.yellow("setup ")
        print(f"[{mark}] {cli.bold(name)}  ({cls.display_name})")
        print(f"          {connector.describe()}")
        allowed = connector.authorized_user_ids()
        if allowed:
            print(f"          allowlist: {', '.join(allowed)}")
        else:
            print(f"          allowlist: {cli.red('EMPTY (open to anyone)')}")
        print(f"          install:   pip install 'ackstreet-agent[{cls.extra}]'")
        print(f"          connect:   ackstreet connect {name}")
        print(f"          run:       ackstreet serve {name}")
        print()

    shared = cfg.get("connectors", "allowed_user_ids", []) or []
    print(cli.dim(f"shared allowlist: {', '.join(str(v) for v in shared) or '(empty)'}"))
    print(cli.dim(f"approval timeout: {cfg.get('connectors', 'approval_timeout', 300)}s"))
    print(cli.dim(f"session TTL: {cfg.get('connectors', 'session_ttl', 3600)}s"))
    return 0


def connector_status_lines(cfg: Config) -> List[Dict[str, Any]]:
    """Compact status rows for `ackstreet doctor`."""
    registry.load_builtin()
    rows: List[Dict[str, Any]] = []
    for name in registry.names():
        cls = registry.get(name)
        try:
            connector = cls(cfg)
            allowed = connector.authorized_user_ids()
        except Exception:  # noqa: BLE001 - doctor must never crash
            allowed = []
        rows.append(
            {
                "name": name,
                "configured": cls.is_configured(cfg),
                "allowlist": allowed,
                "extra": cls.extra,
            }
        )
    return rows


__all__ = ["cmd_connect", "cmd_connectors", "cmd_serve", "connector_status_lines"]
