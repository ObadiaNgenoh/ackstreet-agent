"""Command-line interface for ACKSTREET AGENT.

    ackstreet init            create the config and directory layout
    ackstreet doctor          check config, provider, tools and keys
    ackstreet chat            interactive session
    ackstreet run "<task>"    run one task to completion
    ackstreet plan "<task>"   show the plan the agent would follow
    ackstreet skills ...      inspect and edit saved skills
    ackstreet memory ...      inspect sessions and facts
    ackstreet config ...      show or edit configuration
    ackstreet tools           list available tools
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

from . import __version__
from .agent import Agent, AgentEvent
from .config import Config
from .connectors.commands import (
    cmd_connect,
    cmd_connectors,
    cmd_serve,
    connector_status_lines,
)
from .errors import AckstreetError, ConfigError, ProviderError
from .memory import MemoryStore
from .providers import provider_from_config
from .safety import ApprovalRequest
from .skills.curator import CurationOutcome, SkillCurator
from .skills.registry import SkillRegistry
from .tools import build_default_registry

# --------------------------------------------------------------------------
# Terminal helpers
# --------------------------------------------------------------------------

NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return text if NO_COLOR else f"\033[{code}m{text}\033[0m"


def bold(text: str) -> str:
    return _c(text, "1")


def dim(text: str) -> str:
    return _c(text, "2")


def green(text: str) -> str:
    return _c(text, "32")


def red(text: str) -> str:
    return _c(text, "31")


def yellow(text: str) -> str:
    return _c(text, "33")


def cyan(text: str) -> str:
    return _c(text, "36")


BANNER = r"""
   _   ___ _  __ ___ _____ ___ ___ _____   _   ___ ___ _  _ _____
  /_\ / __| |/ // __|_   _| _ \ __|_   _| /_\ / __| __| \| |_   _|
 / _ \ (__| ' < \__ \ | | |   / _|  | |  / _ \ (_ | _|| .` | | |
/_/ \_\___|_|\_\|___/ |_| |_|_\___| |_| /_/ \_\___|___|_|\_| |_|
"""


def print_banner() -> None:
    print(cyan(BANNER))
    print(f"  self-hosted, self-improving agent  v{__version__}\n")


# --------------------------------------------------------------------------
# Config / agent construction
# --------------------------------------------------------------------------

def load(args: argparse.Namespace) -> Config:
    """Load config, honouring ``--config`` and ``--home``."""
    if getattr(args, "home", None):
        os.environ["ACKSTREET_HOME"] = str(Path(args.home).expanduser())
    target = getattr(args, "config", None)
    cfg = Config.load(Path(target).expanduser() if target else None)

    # CLI overrides win over the file.
    if getattr(args, "provider", None):
        cfg.set("agent", "provider", args.provider)
    if getattr(args, "model", None):
        cfg.set("agent", "model", args.model)
    if getattr(args, "approval_mode", None):
        cfg.set("agent", "approval_mode", args.approval_mode)
    return cfg


class InteractiveApprover:
    """Ask a human whether a dangerous tool call may run (chat mode).

    ``always`` remembers a tool for the rest of the session, so approving
    ``shell`` once does not mean answering the same question twenty times.
    """

    def __init__(self) -> None:
        self.always: set[str] = set()

    def __call__(self, request: ApprovalRequest) -> bool:
        if request.tool in self.always:
            return True

        print()
        print(f"  {yellow('!')} {bold('approval required')} {dim(f'(mode={request.mode})')}")
        shown = request.target.strip().replace("\n", " ")
        if len(shown) > 300:
            shown = shown[:297] + "..."
        print(f"    {bold(request.tool)}  {shown}")
        if request.reason:
            print(dim(f"    reason: {request.reason}"))

        prompt = (
            f"    approve? {bold('[y]')}es / {bold('[n]')}o / "
            f"{bold('[a]')}lways allow '{request.tool}' for this session > "
        )
        while True:
            try:
                answer = input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "always"):
                self.always.add(request.tool)
                return True
            print(dim("    please answer y, n or a"))


def build_agent(
    cfg: Config,
    quiet: bool = False,
    stream: bool = True,
    approver=None,
) -> Agent:
    """Create an :class:`Agent` with a live progress printer."""

    def render(event: AgentEvent) -> None:
        if quiet:
            return
        kind = event.type
        data = event.data

        if kind == "thinking":
            print(dim(f"\n[{data['step']}/{data['total']}] thinking..."), file=sys.stderr)
        elif kind == "plan":
            print(bold("\nPlan:"), file=sys.stderr)
            for index, step in enumerate(data["steps"], start=1):
                print(f"  {index}. {step}", file=sys.stderr)
        elif kind == "tool_start":
            args = json.dumps(data["arguments"], ensure_ascii=False)
            if len(args) > 160:
                args = args[:157] + "..."
            print(f"\n  {cyan('->')} {bold(data['tool'])} {dim(args)}", file=sys.stderr)
        elif kind == "tool_end":
            mark = green("ok") if data["ok"] else red("failed")
            summary = (data.get("summary") or "").strip().replace("\n", " ")[:180]
            print(f"     {mark} {dim(summary)}", file=sys.stderr)
        elif kind == "approval":
            if not data.get("approved"):
                reason = (data.get("reason") or "").strip().replace("\n", " ")
                print(f"     {red('denied')} {dim(reason[:160])}", file=sys.stderr)
            elif data.get("source") == "user":
                print(f"     {green('approved')} {dim('by you')}", file=sys.stderr)
        elif kind == "text" and not stream:
            pass
        elif kind == "skill":
            action = data.get("action")
            if action in ("created", "updated"):
                verb = "Learned new skill" if action == "created" else "Refined skill"
                print(
                    f"\n  {yellow('*')} {verb}: {bold(data['name'])} — {data.get('description', '')}",
                    file=sys.stderr,
                )
        elif kind == "error":
            print(red(f"\n  ! {data.get('message', 'error')}"), file=sys.stderr)

    return Agent(cfg, on_event=render, approver=approver)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    cfg = load(args)
    cfg.ensure_dirs()

    created_config = False
    if not cfg.path.exists() or args.force:
        cfg.save()
        created_config = True

    registry = SkillRegistry(cfg.skills_dir, seeds_dir=_seeds_dir())
    installed = registry.install_seeds(overwrite=args.force)

    MemoryStore(cfg.memory_dir).ensure()

    print_banner()
    print(f"Home:        {cfg.root}")
    print(f"Config:      {cfg.path} {'(written)' if created_config else '(kept existing)'}")
    print(f"Skills:      {cfg.skills_dir}")
    print(f"Memory:      {cfg.memory_dir}")
    print(f"Workspace:   {cfg.workspace}")
    if installed:
        print(f"Seeded:      {', '.join(installed)}")
    print()
    print(bold("Next steps:"))
    print("  1. Export a provider key, e.g.  export OPENAI_API_KEY=sk-...")
    print("     (or run a local model:  ollama serve && ollama pull llama3.1)")
    print("  2. Verify everything:            ackstreet doctor")
    print("  3. Start chatting:               ackstreet chat")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load(args)
    ok = True

    print_banner()
    print(bold("1. Filesystem"))
    for label, path in (
        ("home", cfg.root),
        ("config file", cfg.path),
        ("skills", cfg.skills_dir),
        ("memory", cfg.memory_dir),
        ("workspace", cfg.workspace),
    ):
        exists = path.exists()
        ok = ok and (exists or label == "config file")
        mark = green("ok  ") if exists else yellow("miss")
        print(f"   [{mark}] {label:<12} {path}")

    print(bold("\n2. Configuration"))
    try:
        spec = cfg.resolve_provider()
        print(f"   provider:  {spec.name} (type={spec.type})")
        print(f"   model:     {spec.model or red('NOT SET')}")
        print(f"   base_url:  {spec.base_url or red('NOT SET')}")
        if not spec.model:
            ok = False
    except KeyError as exc:
        print(red(f"   {exc}"))
        return 1

    print(bold("\n3. Credentials (environment)"))
    key_env = cfg.data.get("providers", {}).get(spec.name, {}).get("api_key_env", "")
    if spec.type == "ollama":
        print(f"   [skip] '{spec.name}' is a local backend and needs no API key")
    elif key_env:
        present = bool(os.environ.get(key_env))
        ok = ok and present
        mark = green("ok  ") if present else red("MISS")
        print(f"   [{mark}] {key_env}")

    print(bold("\n4. Backends"))
    for name in cfg.provider_names():
        try:
            probe = provider_from_config(cfg, name, timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            print(f"   [{yellow('skip')}] {name}: {exc}")
            continue
        try:
            healthy, message = probe.health_check()
        except Exception as exc:  # noqa: BLE001
            healthy, message = False, f"{type(exc).__name__}: {exc}"
        finally:
            probe.close()
        mark = green("ok  ") if healthy else yellow("warn")
        print(f"   [{mark}] {name}: {message}")
        if name == spec.name and not healthy:
            ok = False

    print(bold("\n5. Tools"))
    registry = build_default_registry(cfg, SkillRegistry(cfg.skills_dir))
    print(f"   {len(registry.names())} tool(s): {', '.join(registry.names())}")

    print(bold("\n5. Approval gate"))
    policy = cfg.approval_policy()
    dangerous = [
        tool.name for tool in registry.tools() if getattr(tool, "dangerous", False)
    ]
    print(f"   mode:      {policy.describe()}")
    print(f"   guarding:  {', '.join(dangerous) or '(none)'}")
    if policy.allowlist:
        print(f"   allowlist: {', '.join(policy.allowlist)}")
    if policy.denylist:
        print(f"   denylist:  {', '.join(policy.denylist)}")
    if policy.mode == "auto":
        print(
            yellow(
                "   [warn] dangerous tools run unattended. Use --approval-mode ask "
                "or an allowlist for untrusted input."
            )
        )

    print(bold("\n6. Skills and memory"))
    skills = SkillRegistry(cfg.skills_dir, seeds_dir=_seeds_dir()).list()
    store = MemoryStore(cfg.memory_dir)
    stats = store.stats()
    print(f"   skills:   {len(skills)} ({', '.join(s.name for s in skills) or 'none'})")
    print(f"   sessions: {stats['sessions']}, facts: {stats['facts']}")

    print(bold("\n7. Chat connectors"))
    try:
        rows = connector_status_lines(cfg)
    except Exception as exc:  # noqa: BLE001 - doctor must never crash
        rows = []
        print(f"   [{yellow('warn')}] could not inspect connectors: {exc}")
    if not rows:
        print("   (no connectors registered)")
    for row in rows:
        mark = green("ok  ") if row["configured"] else yellow("setup")
        scope = ", ".join(row["allowlist"]) if row["allowlist"] else red("EMPTY (open)")
        print(f"   [{mark}] {row['name']}: allowlist {scope}")
        if not row["configured"]:
            print(dim(f"           ackstreet connect {row['name']}"))
    if any(not row["allowlist"] for row in rows):
        print(
            yellow(
                "   [warn] an empty allowlist means anyone who can reach the bot may "
                "drive this agent. Set connectors.<name>.allowed_user_ids."
            )
        )

    print()
    if ok:
        print(green(bold("All critical checks passed.")))
        return 0
    print(yellow(bold("Some checks need attention (see MISS/warn above).")))
    return 1


def _render_result(result, show_steps: bool = False) -> None:
    if show_steps and result.steps:
        print(bold("\nTool calls:"), file=sys.stderr)
        for step in result.steps:
            mark = green("ok") if step["ok"] else red("fail")
            print(
                f"  {step['step']}. [{mark}] {step['tool']} "
                f"{dim(json.dumps(step['arguments'], ensure_ascii=False)[:120])}",
                file=sys.stderr,
            )
    if result.skills_learned:
        print(yellow(f"\nSkills saved: {', '.join(result.skills_learned)}"), file=sys.stderr)
    print()
    print(bold("Answer:"))
    print(result.text or "(no answer)")
    print()
    print(
        dim(
            f"[{len(result.steps)} tool call(s), {result.elapsed:.1f}s, "
            f"stop={result.stopped_reason}]"
        )
    )


def _apply_approval_flags(args: argparse.Namespace, cfg: Config) -> None:
    """Honour ``--yes`` / ``--no-approval`` before the agent is built.

    Both flags mean the same thing: grant approval for dangerous tools without
    prompting. ``--yes`` is the canonical spelling (it answers the prompt),
    ``--no-approval`` is the explicit alias for skipping the gate.
    """
    if getattr(args, "yes", False) or getattr(args, "no_approval", False):
        cfg.set("agent", "approval_mode", "auto")


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load(args)
    cfg.ensure_dirs()
    _apply_approval_flags(args, cfg)
    # `run` is non-interactive: no approver, so ask/allowlist mode refuses
    # dangerous calls instead of blocking on a prompt nobody can answer.
    agent = build_agent(cfg, quiet=args.quiet, stream=False)
    task = " ".join(args.task).strip()
    if not task:
        print(red("No task given."), file=sys.stderr)
        return 2
    try:
        result = agent.run(task, max_steps=args.max_steps, plan_first=args.plan, stream=False)
    except ConfigError as exc:
        print(red(str(exc)), file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(red(f"Provider error: {exc}"), file=sys.stderr)
        return 3
    _render_result(result, show_steps=args.verbose)
    if args.json:
        print(json.dumps(
            {
                "text": result.text,
                "steps": result.steps,
                "skills_learned": result.skills_learned,
                "stopped_reason": result.stopped_reason,
                "elapsed": result.elapsed,
                "session_id": result.session_id,
            },
            indent=2,
        ))
    return 0 if result.stopped_reason != "provider_error" else 3


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = load(args)
    agent = build_agent(cfg, quiet=True, stream=False)
    task = " ".join(args.task).strip()
    steps = agent.plan(task)
    if not steps:
        print(yellow("No plan produced (check provider credentials with `ackstreet doctor`)."))
        return 1
    print(bold(f"Plan for: {task}\n"))
    for index, step in enumerate(steps, start=1):
        print(f"  {index}. {step}")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    cfg = load(args)
    cfg.ensure_dirs()
    _apply_approval_flags(args, cfg)
    approver = None if args.yes else InteractiveApprover()
    agent = build_agent(cfg, quiet=args.quiet, stream=True, approver=approver)

    print_banner()
    spec = cfg.resolve_provider()
    print(f"provider {bold(spec.name)}  model {bold(spec.model or '(unset)')}")
    print(f"approvals {bold(agent.approvals.describe())}")
    print(dim("Commands: /help /tools /skills /memory /clear /exit\n"))

    while True:
        try:
            user_input = input(bold("you > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input in ("/exit", "/quit", ":q"):
            break
        if user_input == "/help":
            print(
                "  /tools    list available tools\n"
                "  /skills   list saved skills\n"
                "  /approvals  show the approval gate state\n"
                "  /memory   show memory stats\n"
                "  /clear    reset the conversation (skills persist)\n"
                "  /exit     leave the session"
            )
            continue
        if user_input == "/tools":
            print("  " + ", ".join(agent.tools.names()))
            continue
        if user_input == "/skills":
            for skill in agent.skills.list():
                print(f"  - {skill.name}: {skill.description}")
            if not agent.skills.list():
                print("  (no skills saved yet)")
            continue
        if user_input == "/approvals":
            print(f"  mode: {agent.approvals.describe()}")
            if isinstance(approver, InteractiveApprover) and approver.always:
                print(f"  always allowed this session: {', '.join(sorted(approver.always))}")
            continue
        if user_input == "/memory":
            print(f"  {agent.memory.stats()}")
            continue
        if user_input == "/clear":
            agent.reset()
            print(dim("  conversation cleared"))
            continue

        try:
            result = agent.chat_turn(user_input, stream=not args.no_stream)
        except ConfigError as exc:
            print(red(str(exc)))
            continue
        except ProviderError as exc:
            print(red(f"Provider error: {exc}"))
            continue
        except KeyboardInterrupt:
            print(yellow("\n  interrupted"))
            continue

        print()
        if result.text:
            print(result.text)
        if result.skills_learned:
            print(yellow(f"\n[saved skill: {', '.join(result.skills_learned)}]"))
        print()

    print(dim("bye"))
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    cfg = load(args)
    registry = build_default_registry(cfg, SkillRegistry(cfg.skills_dir))
    policy = cfg.approval_policy()
    print(bold(f"{len(registry.names())} tool(s) available"))
    print(dim(f"approval mode: {policy.describe()}\n"))
    for tool in registry.tools():
        if getattr(tool, "dangerous", False):
            needs, why = policy.requires_approval(tool.name, {})
            state = "needs approval" if needs else "auto-approved"
            flag = red(f" [dangerous \u2014 {state}]")
        else:
            flag = ""
        print(f"  {bold(tool.name)}{flag}")
        print(f"      {tool.description}")
        params = tool.parameters.get("properties", {})
        required = set(tool.parameters.get("required", []))
        for param, spec in params.items():
            marker = "*" if param in required else " "
            print(f"      {marker} {param}: {spec.get('description', spec.get('type', ''))}")
        print()
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    cfg = load(args)
    cfg.ensure_dirs()
    registry = SkillRegistry(cfg.skills_dir, seeds_dir=_seeds_dir())

    if args.skill_action == "list":
        skills = registry.list()
        if not skills:
            print(yellow("No skills saved yet. Run a task and the agent will learn one."))
            return 0
        print(bold(f"{len(skills)} skill(s) in {cfg.skills_dir}\n"))
        for skill in skills:
            print(f"  {bold(skill.name)}  {dim('v' + skill.version)}")
            print(f"    {skill.description or '(no description)'}")
            print(dim(f"    {skill.path}"))
            print()
        return 0

    if args.skill_action == "show":
        skill = registry.get(args.name)
        if skill is None:
            print(red(f"No skill named '{args.name}'."), file=sys.stderr)
            return 1
        print(skill.render())
        return 0

    if args.skill_action == "create":
        body = args.body or ""
        if args.body_file:
            body = Path(args.body_file).read_text(encoding="utf-8")
        if not body:
            body = (
                "## When to Use\n(when this applies)\n\n"
                "## Steps\n1. (first step)\n\n"
                "## Pitfalls\n- (what goes wrong)\n\n"
                "## Verification\n- (how to confirm success)\n"
            )
        skill = registry.create(
            name=args.name,
            description=args.description or "(no description)",
            body=body,
            tags=args.tags or [],
            source="manual",
            overwrite=args.force,
        )
        print(green(f"Created skill '{skill.name}' at {skill.path}"))
        return 0

    if args.skill_action == "edit":
        path = registry.get(args.name)
        if path is None or path.path is None:
            print(red(f"No skill named '{args.name}'."), file=sys.stderr)
            return 1
        editor = os.environ.get("EDITOR", "nano")
        print(dim(f"Opening {path.path} with {editor}"))
        return os.system(f"{editor} {path.path}")

    if args.skill_action == "delete":
        if registry.delete(args.name):
            print(green(f"Deleted skill '{args.name}'"))
            return 0
        print(red(f"No skill named '{args.name}'."), file=sys.stderr)
        return 1

    if args.skill_action == "search":
        hits = registry.search(args.query)
        if not hits:
            print(yellow(f"No skills matched '{args.query}'."))
            return 0
        for skill in hits:
            print(f"  {bold(skill.name)}: {skill.description}")
        return 0

    if args.skill_action == "curate":
        transcript = Path(args.transcript).read_text(encoding="utf-8")
        curator = SkillCurator(registry, provider_from_config(cfg))
        outcome: CurationOutcome = curator.curate(
            task=args.task or "(manual curation)", steps=[], summary=transcript, force=True
        )
        print(outcome.reason)
        return 0 if outcome.changed else 1

    print(red("Unknown skills subcommand"), file=sys.stderr)
    return 2


def cmd_memory(args: argparse.Namespace) -> int:
    cfg = load(args)
    store = MemoryStore(cfg.memory_dir)

    if args.memory_action == "stats":
        stats = store.stats()
        print(bold("Memory"))
        for key, value in stats.items():
            print(f"  {key}: {value}")
        return 0

    if args.memory_action == "sessions":
        entries = store.sessions()[: args.limit]
        if not entries:
            print(yellow("No sessions recorded yet."))
            return 0
        for entry in entries:
            print(f"  {bold(entry['id'])}  {dim(entry.get('started', ''))}")
            print(f"    task: {entry.get('task', '')[:100]}")
            if entry.get("summary"):
                print(f"    summary: {entry['summary'][:120]}")
            if entry.get("skills_learned"):
                print(f"    skills: {', '.join(entry['skills_learned'])}")
            print()
        return 0

    if args.memory_action == "show":
        session = store.load_session(args.session_id)
        if session is None:
            print(red(f"No session '{args.session_id}'."), file=sys.stderr)
            return 1
        print(json.dumps(session.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.memory_action == "recall":
        block = store.recall(args.query, limit=args.limit)
        print(block or yellow("Nothing relevant found in memory."))
        return 0

    if args.memory_action == "remember":
        entry = store.remember(args.fact, tags=args.tags or [])
        print(green(f"Remembered: {entry['fact']}"))
        return 0

    if args.memory_action == "facts":
        facts = store.facts()
        if not facts:
            print(yellow("No facts recorded yet."))
            return 0
        for index, fact in enumerate(facts):
            print(f"  [{index}] {fact.get('fact')} {dim(str(fact.get('tags', [])))}")
        return 0

    if args.memory_action == "forget":
        if store.forget(args.index):
            print(green(f"Forgot fact #{args.index}"))
            return 0
        print(red(f"No fact at index {args.index}."), file=sys.stderr)
        return 1

    print(red("Unknown memory subcommand"), file=sys.stderr)
    return 2


def _parse_config_value(raw: str) -> Any:
    """Interpret a ``config set`` value as TOML-ish rather than always a string.

    Without this, ``config set agent.approval_allowlist '["ls"]'`` would store
    the literal text and the gate would then match against individual
    characters. JSON is tried first so lists and objects round-trip.
    """
    text = raw.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return ""
    if text[0] in "[{":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return raw


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load(args)

    if args.config_action == "show":
        print(cfg.to_toml())
        return 0

    if args.config_action == "path":
        print(cfg.path)
        return 0

    if args.config_action == "edit":
        cfg.path.parent.mkdir(parents=True, exist_ok=True)
        if not cfg.path.exists():
            cfg.save()
        editor = os.environ.get("EDITOR", "nano")
        print(dim(f"Opening {cfg.path} with {editor}"))
        return os.system(f"{editor} {cfg.path}")

    if args.config_action == "set":
        if "." not in args.key:
            print(red("key must be in the form section.key"), file=sys.stderr)
            return 2
        section, key = args.key.split(".", 1)
        value: Any = _parse_config_value(args.value)
        cfg.set(section, key, value)
        cfg.save()
        print(green(f"Set {section}.{key} = {value!r} in {cfg.path}"))
        return 0

    print(red("Unknown config subcommand"), file=sys.stderr)
    return 2


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def _seeds_dir() -> Optional[Path]:
    candidate = Path(__file__).parent / "seeds" / "skills"
    return candidate if candidate.exists() else None


def _add_global_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Path to a config.toml file")
    parser.add_argument("--home", help="Override the ACKSTREET home directory")
    parser.add_argument("--provider", help="Override the active provider")
    parser.add_argument("--model", help="Override the model name")
    parser.add_argument(
        "--approval-mode",
        choices=["auto", "ask", "allowlist"],
        help="Override agent.approval_mode for this run",
    )


def _add_approval_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Approve dangerous tools without prompting (sets approval_mode=auto)",
    )
    parser.add_argument(
        "--no-approval",
        action="store_true",
        help="Alias for --yes: skip the approval gate entirely",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ackstreet",
        description="ACKSTREET AGENT — a self-hosted, self-improving AI agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ackstreet init\n"
            '  ackstreet run "count the python files here and write a report"\n'
            "  ackstreet chat --plan\n"
            "  ackstreet skills list\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"ackstreet {__version__}")
    _add_global_flags(parser)

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_init = sub.add_parser("init", help="Create config and directory layout")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config and seeds")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="Check config, providers, keys and tools")
    p_doctor.set_defaults(func=cmd_doctor)

    p_run = sub.add_parser("run", help="Run one task to completion")
    p_run.add_argument("task", nargs="+", help="The task, in quotes")
    p_run.add_argument("--max-steps", type=int, default=None, help="Maximum tool-calling steps")
    p_run.add_argument("--plan", action="store_true", help="Produce a plan before acting")
    p_run.add_argument("--quiet", action="store_true", help="Hide progress output")
    p_run.add_argument("--verbose", action="store_true", help="Show every tool call")
    p_run.add_argument("--json", action="store_true", help="Also print a machine-readable result")
    _add_approval_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_plan = sub.add_parser("plan", help="Show the plan the agent would follow")
    p_plan.add_argument("task", nargs="+", help="The task, in quotes")
    p_plan.set_defaults(func=cmd_plan)

    p_chat = sub.add_parser("chat", help="Start an interactive session")
    p_chat.add_argument("--plan", action="store_true", help="Plan before each task")
    p_chat.add_argument("--quiet", action="store_true", help="Hide progress output")
    p_chat.add_argument("--no-stream", action="store_true", help="Disable token streaming")
    _add_approval_flags(p_chat)
    p_chat.set_defaults(func=cmd_chat)

    p_tools = sub.add_parser("tools", help="List available tools and their arguments")
    p_tools.set_defaults(func=cmd_tools)

    # -- skills ------------------------------------------------------------
    p_skills = sub.add_parser("skills", help="Inspect and edit saved skills")
    skills_sub = p_skills.add_subparsers(dest="skill_action", metavar="<action>")

    skills_sub.add_parser("list", help="List all skills")

    s_show = skills_sub.add_parser("show", help="Print one skill in full")
    s_show.add_argument("name")

    s_create = skills_sub.add_parser("create", help="Create a skill by hand")
    s_create.add_argument("name")
    s_create.add_argument("--description", default="")
    s_create.add_argument("--body", default="")
    s_create.add_argument("--body-file", default="")
    s_create.add_argument("--tags", nargs="*", default=[])
    s_create.add_argument("--force", action="store_true", help="Overwrite if it exists")

    s_edit = skills_sub.add_parser("edit", help="Open a skill in $EDITOR")
    s_edit.add_argument("name")

    s_delete = skills_sub.add_parser("delete", help="Delete a skill")
    s_delete.add_argument("name")

    s_search = skills_sub.add_parser("search", help="Search skills by keyword")
    s_search.add_argument("query")

    s_curate = skills_sub.add_parser("curate", help="Run the curator over a transcript file")
    s_curate.add_argument("transcript")
    s_curate.add_argument("--task", default="")

    p_skills.set_defaults(func=cmd_skills, skill_action="list")

    # -- memory ------------------------------------------------------------
    p_memory = sub.add_parser("memory", help="Inspect sessions and durable facts")
    memory_sub = p_memory.add_subparsers(dest="memory_action", metavar="<action>")

    memory_sub.add_parser("stats", help="Show memory statistics")

    m_sessions = memory_sub.add_parser("sessions", help="List recent sessions")
    m_sessions.add_argument("--limit", type=int, default=20)

    m_show = memory_sub.add_parser("show", help="Print one session as JSON")
    m_show.add_argument("session_id")

    m_recall = memory_sub.add_parser("recall", help="Show what memory recalls for a query")
    m_recall.add_argument("query")
    m_recall.add_argument("--limit", type=int, default=8)

    m_remember = memory_sub.add_parser("remember", help="Record a durable fact")
    m_remember.add_argument("fact")
    m_remember.add_argument("--tags", nargs="*", default=[])

    memory_sub.add_parser("facts", help="List recorded facts")

    m_forget = memory_sub.add_parser("forget", help="Delete a fact by index")
    m_forget.add_argument("index", type=int)

    p_memory.set_defaults(func=cmd_memory, memory_action="stats")

    # -- config ------------------------------------------------------------
    p_config = sub.add_parser("config", help="Show or edit configuration")
    config_sub = p_config.add_subparsers(dest="config_action", metavar="<action>")

    config_sub.add_parser("show", help="Print the effective config as TOML")
    config_sub.add_parser("path", help="Print the config file path")
    config_sub.add_parser("edit", help="Open the config in $EDITOR")

    c_set = config_sub.add_parser("set", help="Set a value, e.g. config set agent.model gpt-4o")
    c_set.add_argument("key", help="section.key")
    c_set.add_argument("value")

    p_config.set_defaults(func=cmd_config, config_action="show")

    # -- connectors --------------------------------------------------------
    p_connectors = sub.add_parser(
        "connectors", help="List chat-platform connectors and their state"
    )
    p_connectors.set_defaults(func=cmd_connectors)

    p_connect = sub.add_parser(
        "connect", help="Connect a chat platform (telegram, whatsapp)"
    )
    p_connect.add_argument(
        "platform",
        nargs="?",
        help="Platform to configure. Omit to list them.",
    )
    p_connect.add_argument(
        "--token",
        help="Telegram bot token from @BotFather",
    )
    p_connect.add_argument(
        "--session-path",
        dest="session_path",
        help="Where to persist the WhatsApp session (default: <home>/whatsapp/session.db)",
    )
    p_connect.add_argument(
        "--allow-user",
        dest="allow_user",
        action="append",
        default=[],
        help="Add a user id to this connector's allowlist (repeatable)",
    )
    p_connect.add_argument(
        "--no-verify",
        dest="no_verify",
        action="store_true",
        help="Store the Telegram token without calling getMe to check it",
    )
    p_connect.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for the WhatsApp QR scan",
    )
    p_connect.set_defaults(func=cmd_connect)

    p_serve = sub.add_parser(
        "serve", help="Run a connector's listener and answer messages with the agent"
    )
    p_serve.add_argument("platform", help="Connector to run (telegram, whatsapp)")
    p_serve.add_argument(
        "--quiet", action="store_true", help="Hide connector-level logging"
    )
    _add_approval_flags(p_serve)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n" + yellow("interrupted"))
        return 130
    except AckstreetError as exc:
        print(red(f"error: {exc}"), file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(red(f"error: {exc}"), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
