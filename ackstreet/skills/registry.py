"""Skill registry: durable, human-readable, self-authored capabilities.

A *skill* is a markdown file with YAML frontmatter stored under
``~/.ackstreet/skills/<slug>.md``. Skills are how ACKSTREET AGENT improves over
time: after finishing a task it can write down the reusable procedure, and on
later sessions it loads a compact index of those skills into its system prompt.

The format is deliberately plain text so a human can read, diff, and hand-edit
every skill the agent has learned.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..errors import SkillError

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
version: 1.0.0
author: {author}
created: {created}
tags: {tags}
source: {source}
---

# Skill: {title}

## When to Use
{when_to_use}

## Steps
{steps}

## Pitfalls
{pitfalls}

## Verification
{verification}
"""


# --------------------------------------------------------------------------
# Frontmatter parsing (no third-party dependency required)
# --------------------------------------------------------------------------

def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return ""
    if text[0] in "\"'" and text[-1] == text[0] and len(text) >= 2:
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Split ``---`` frontmatter from the markdown body.

    Tries PyYAML when it is installed (handles nested structures and quoting
    properly) and otherwise falls back to a small parser covering the flat
    ``key: value`` shape skills actually use.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw_meta, body = match.group(1), match.group(2)

    try:  # Prefer a real YAML parser when available.
        import yaml  # type: ignore

        loaded = yaml.safe_load(raw_meta)
        if isinstance(loaded, dict):
            return loaded, body
    except Exception:  # noqa: BLE001 - fall through to the minimal parser
        pass

    meta: Dict[str, Any] = {}
    for line in raw_meta.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = _parse_scalar(value)
    return meta, body


def dump_frontmatter(meta: Dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            rendered = "[" + ", ".join(str(v) for v in value) + "]"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.lstrip("\n")


def slugify(text: str) -> str:
    """Turn arbitrary text into a valid skill slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:64] or "untitled-skill"


# --------------------------------------------------------------------------
# Skill record
# --------------------------------------------------------------------------

@dataclass
class Skill:
    """One loaded skill."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    created: str = ""
    updated: str = ""
    tags: List[str] = field(default_factory=list)
    source: str = "manual"
    body: str = ""
    path: Optional[Path] = None

    @property
    def title(self) -> str:
        for line in self.body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return self.name

    @property
    def size(self) -> int:
        return len(self.body)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "created": self.created,
            "updated": self.updated,
            "tags": list(self.tags),
            "source": self.source,
            "body": self.body,
            "path": str(self.path) if self.path else None,
        }

    def to_markdown(self) -> str:
        meta = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "created": self.created,
            "updated": self.updated or self.created,
            "tags": self.tags,
            "source": self.source,
        }
        return dump_frontmatter(meta, self.body)

    def summary_line(self) -> str:
        tags = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"- **{self.name}**: {self.description or '(no description)'}{tags}"

    def render(self) -> str:
        """Full text the agent reads when it loads this skill."""
        return f"# Skill: {self.name}\n\n{self.body.strip()}\n"


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

class SkillRegistry:
    """Filesystem-backed store of skills."""

    def __init__(self, skills_dir: Path, seeds_dir: Optional[Path] = None) -> None:
        self.dir = Path(skills_dir)
        self.seeds_dir = Path(seeds_dir) if seeds_dir else None

    # -- lifecycle ---------------------------------------------------------

    def ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    def install_seeds(self, overwrite: bool = False) -> List[str]:
        """Copy bundled starter skills into the user's skills directory.

        Never clobbers an existing skill unless *overwrite* is set, so a user's
        learned version always wins over the shipped example.
        """
        if not self.seeds_dir or not self.seeds_dir.exists():
            return []
        self.ensure_dir()
        installed: List[str] = []
        for source in sorted(self.seeds_dir.glob("*.md")):
            destination = self.dir / source.name
            if destination.exists() and not overwrite:
                continue
            shutil.copyfile(source, destination)
            installed.append(source.stem)
        return installed

    # -- read --------------------------------------------------------------

    def paths(self) -> List[Path]:
        if not self.dir.exists():
            return []
        return sorted(p for p in self.dir.glob("*.md") if p.is_file())

    def list(self) -> List[Skill]:
        skills: List[Skill] = []
        for path in self.paths():
            try:
                skills.append(self._load_path(path))
            except SkillError:
                continue
        return sorted(skills, key=lambda s: s.name)

    def names(self) -> List[str]:
        return [s.name for s in self.list()]

    def get(self, name: str) -> Optional[Skill]:
        slug = slugify(name)
        direct = self.dir / f"{slug}.md"
        if direct.exists():
            return self._load_path(direct)
        # Fall back to matching the stored ``name`` field.
        for skill in self.list():
            if skill.name == name or slugify(skill.name) == slug:
                return skill
        return None

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def _load_path(self, path: Path) -> Skill:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError(f"cannot read skill {path}: {exc}") from exc

        meta, body = parse_frontmatter(text)
        name = str(meta.get("name") or path.stem)
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        return Skill(
            name=slugify(name),
            description=str(meta.get("description") or ""),
            version=str(meta.get("version") or "1.0.0"),
            author=str(meta.get("author") or ""),
            created=str(meta.get("created") or ""),
            updated=str(meta.get("updated") or ""),
            tags=[str(t) for t in tags],
            source=str(meta.get("source") or "manual"),
            body=body.strip(),
            path=path,
        )

    # -- write -------------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        body: str,
        tags: Optional[Iterable[str]] = None,
        author: str = "ackstreet-agent",
        source: str = "manual",
        overwrite: bool = False,
    ) -> Skill:
        """Write a new skill. Raises if it exists and ``overwrite`` is false."""
        slug = slugify(name)
        if not SLUG_RE.match(slug):
            raise SkillError(f"invalid skill name: {name!r}")

        self.ensure_dir()
        path = self.dir / f"{slug}.md"

        if path.exists() and not overwrite:
            raise SkillError(
                f"skill '{slug}' already exists at {path}. "
                "Use update_skill to change it, or pass overwrite=true."
            )

        now = _dt.date.today().isoformat()
        created = now
        if path.exists():
            try:
                created = self._load_path(path).created or now
            except SkillError:
                created = now

        skill = Skill(
            name=slug,
            description=description.strip(),
            author=author,
            created=created,
            updated=now,
            tags=[slugify(t) for t in (tags or [])],
            source=source,
            body=body.strip(),
            path=path,
        )
        path.write_text(skill.to_markdown(), encoding="utf-8")
        return skill

    def update(
        self,
        name: str,
        body: Optional[str] = None,
        description: Optional[str] = None,
        append: bool = False,
        tags: Optional[Iterable[str]] = None,
    ) -> Skill:
        """Modify an existing skill in place."""
        skill = self.get(name)
        if skill is None:
            raise SkillError(f"skill '{name}' does not exist")

        if body is not None:
            if append and skill.body:
                skill.body = f"{skill.body.rstrip()}\n\n{body.strip()}"
            else:
                skill.body = body.strip()
        if description is not None:
            skill.description = description.strip()
        if tags is not None:
            skill.tags = [slugify(t) for t in tags]
        skill.updated = _dt.date.today().isoformat()

        assert skill.path is not None
        skill.path.write_text(skill.to_markdown(), encoding="utf-8")
        return skill

    def delete(self, name: str) -> bool:
        skill = self.get(name)
        if skill is None or skill.path is None:
            return False
        skill.path.unlink(missing_ok=True)
        return True

    # -- rendering ---------------------------------------------------------

    def render_index(self, max_description: int = 160) -> str:
        """Compact catalogue for the system prompt.

        Only names and descriptions go in — the agent loads a full skill body
        on demand via the ``load_skill`` tool, keeping the prompt small even
        with hundreds of skills.
        """
        skills = self.list()
        if not skills:
            return ""

        lines = [
            "## Skills you have learned",
            "",
            "These are reusable procedures you saved in earlier sessions. "
            "Call `load_skill` with a name to read the full procedure before "
            "starting a task that matches.",
            "",
        ]
        for skill in skills:
            description = skill.description or "(no description)"
            if len(description) > max_description:
                description = description[: max_description - 3] + "..."
            lines.append(f"- `{skill.name}` — {description}")
        return "\n".join(lines)

    def search(self, term: str) -> List[Skill]:
        """Case-insensitive match across name, description, tags and body."""
        needle = (term or "").lower().strip()
        if not needle:
            return []
        hits: List[Skill] = []
        for skill in self.list():
            haystack = " ".join(
                [skill.name, skill.description, " ".join(skill.tags), skill.body]
            ).lower()
            if needle in haystack:
                hits.append(skill)
        return hits

    def to_markdown_report(self) -> str:
        skills = self.list()
        if not skills:
            return "No skills saved yet."
        lines = [f"# Skills ({len(skills)})", ""]
        for skill in skills:
            lines.append(f"## {skill.name}")
            lines.append("")
            lines.append(f"- description: {skill.description or '(none)'}")
            lines.append(f"- version: {skill.version}")
            lines.append(f"- source: {skill.source}")
            lines.append(f"- tags: {', '.join(skill.tags) or '(none)'}")
            lines.append(f"- updated: {skill.updated or skill.created or 'unknown'}")
            lines.append(f"- file: {skill.path}")
            lines.append("")
        return "\n".join(lines)


__all__ = [
    "Skill",
    "SkillRegistry",
    "dump_frontmatter",
    "parse_frontmatter",
    "slugify",
]
