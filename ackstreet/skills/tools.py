"""Tools that let the agent read and write its own skills."""

from __future__ import annotations

from typing import Any, List

from ..config import Config
from ..errors import SkillError
from ..tools.base import Tool, ToolResult
from .registry import SkillRegistry


class ListSkillsTool(Tool):
    name = "list_skills"
    description = (
        "List every skill you have saved, with descriptions. Call this to check whether "
        "you already have a procedure for the task in front of you."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        super().__init__(config)
        self.registry = registry

    def run(self, **_: Any) -> ToolResult:
        skills = self.registry.list()
        if not skills:
            return ToolResult.success("No skills saved yet.")
        lines = [f"{len(skills)} saved skill(s):", ""]
        for skill in skills:
            lines.append(skill.summary_line())
            if skill.updated or skill.created:
                lines.append(f"  (updated {skill.updated or skill.created})")
        return ToolResult.success("\n".join(lines), count=len(skills))


class LoadSkillTool(Tool):
    name = "load_skill"
    description = (
        "Read the full text of a saved skill so you can follow its procedure. "
        "Load the skill before starting a task it matches — do not guess from the description."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill slug, e.g. 'deploy-static-site'."}
        },
        "required": ["name"],
    }

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        super().__init__(config)
        self.registry = registry

    def run(self, name: str, **_: Any) -> ToolResult:
        skill = self.registry.get(name)
        if skill is None:
            available = ", ".join(self.registry.names()) or "(none)"
            return ToolResult.failure(
                f"no skill named '{name}'. Available skills: {available}"
            )
        return ToolResult.success(skill.render(), name=skill.name)


class SaveSkillTool(Tool):
    name = "save_skill"
    description = (
        "Save a reusable procedure as a skill so it is available in future sessions. "
        "Use it after you work out HOW to do something you may need to do again. "
        "Write the body with sections: ## When to Use, ## Steps, ## Pitfalls, ## Verification."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short kebab-case slug, e.g. 'build-python-package'.",
            },
            "description": {
                "type": "string",
                "description": "One line: what the skill does and when to use it (these words are how you will find it later).",
            },
            "body": {
                "type": "string",
                "description": "Markdown body of the skill.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional lowercase tags.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Replace an existing skill of the same name (default false).",
            },
        },
        "required": ["name", "description", "body"],
    }
    dangerous = True

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        super().__init__(config)
        self.registry = registry

    def run(
        self,
        name: str,
        description: str,
        body: str,
        tags: List[str] | None = None,
        overwrite: bool = False,
        **_: Any,
    ) -> ToolResult:
        if not self.config.get("skills", "enabled", True):
            return ToolResult.failure("skills are disabled (skills.enabled = false)")

        if len(body or "") < 40:
            return ToolResult.failure(
                "the skill body is too short to be useful — include the actual steps"
            )

        try:
            skill = self.registry.create(
                name=name,
                description=description,
                body=body,
                tags=tags or [],
                source="agent",
                overwrite=overwrite,
            )
        except SkillError as exc:
            return ToolResult.failure(str(exc))

        return ToolResult.success(
            f"saved skill '{skill.name}' to {skill.path}\n"
            f"description: {skill.description}",
            name=skill.name,
            path=str(skill.path),
        )


class UpdateSkillTool(Tool):
    name = "update_skill"
    description = (
        "Revise an existing skill — use this when you learn a better way to do "
        "something a saved skill already covers, rather than creating a near-duplicate."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill slug to update."},
            "body": {"type": "string", "description": "New body, or the text to append."},
            "description": {"type": "string", "description": "Optional new description."},
            "append": {
                "type": "boolean",
                "description": "Append to the existing body instead of replacing it.",
            },
        },
        "required": ["name"],
    }
    dangerous = True

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        super().__init__(config)
        self.registry = registry

    def run(
        self,
        name: str,
        body: str | None = None,
        description: str | None = None,
        append: bool = False,
        **_: Any,
    ) -> ToolResult:
        try:
            skill = self.registry.update(
                name, body=body, description=description, append=append
            )
        except SkillError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(
            f"updated skill '{skill.name}' ({skill.path})", name=skill.name
        )


class SearchSkillsTool(Tool):
    name = "search_skills"
    description = "Search your saved skills by keyword across names, descriptions, tags and bodies."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword or phrase to look for."}
        },
        "required": ["query"],
    }

    def __init__(self, config: Config, registry: SkillRegistry) -> None:
        super().__init__(config)
        self.registry = registry

    def run(self, query: str, **_: Any) -> ToolResult:
        hits = self.registry.search(query)
        if not hits:
            return ToolResult.success(
                f"no skills matched '{query}' ({len(self.registry.list())} skills exist)"
            )
        lines = [f"{len(hits)} skill(s) matching '{query}':"]
        lines.extend(skill.summary_line() for skill in hits)
        return ToolResult.success("\n".join(lines), count=len(hits))


def build_skill_tools(config: Config, registry: SkillRegistry) -> List[Tool]:
    """All skill tools, bound to one registry."""
    return [
        ListSkillsTool(config, registry),
        LoadSkillTool(config, registry),
        SaveSkillTool(config, registry),
        UpdateSkillTool(config, registry),
        SearchSkillsTool(config, registry),
    ]


__all__ = [
    "ListSkillsTool",
    "LoadSkillTool",
    "SaveSkillTool",
    "SearchSkillsTool",
    "UpdateSkillTool",
    "build_skill_tools",
]
