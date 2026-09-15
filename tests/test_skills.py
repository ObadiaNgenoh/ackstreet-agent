"""Tests for the skill registry, frontmatter parsing and the curator."""

from __future__ import annotations

from pathlib import Path

import pytest

from ackstreet.errors import SkillError
from ackstreet.skills.curator import SkillCurator
from ackstreet.skills.registry import (
    SkillRegistry,
    dump_frontmatter,
    parse_frontmatter,
    slugify,
)

SAMPLE_BODY = """\
## When to Use
When you need to do the thing.

## Steps
1. Do the first part.
2. Do the second part.

## Pitfalls
- Doing it in the wrong order.

## Verification
- The thing is done.
"""


# -- frontmatter -----------------------------------------------------------

def test_parse_frontmatter_basic() -> None:
    text = "---\nname: my-skill\ntags: [a, b]\nversion: 2.0.0\n---\n\n# Body\n\ntext here\n"
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "my-skill"
    assert meta["tags"] == ["a", "b"]
    assert meta["version"] == "2.0.0"
    assert "# Body" in body


def test_parse_frontmatter_absent() -> None:
    meta, body = parse_frontmatter("just markdown\n")
    assert meta == {}
    assert body == "just markdown\n"


def test_dump_frontmatter_roundtrip() -> None:
    meta = {"name": "s", "tags": ["x", "y"], "version": "1.0.0"}
    text = dump_frontmatter(meta, "body text\n")
    reparsed, body = parse_frontmatter(text)
    assert reparsed["name"] == "s"
    assert reparsed["tags"] == ["x", "y"]
    assert "body text" in body


def test_slugify() -> None:
    assert slugify("Deploy Static Site!") == "deploy-static-site"
    assert slugify("  multiple   spaces  ") == "multiple-spaces"
    assert slugify("") == "untitled-skill"
    assert slugify("UPPER_case-123") == "upper-case-123"


# -- registry --------------------------------------------------------------

def test_create_and_get(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    skill = registry.create(
        name="demo-skill",
        description="A demo skill for testing.",
        body=SAMPLE_BODY,
        tags=["demo", "test"],
    )
    assert skill.name == "demo-skill"
    assert (tmp_path / "skills" / "demo-skill.md").exists()

    loaded = registry.get("demo-skill")
    assert loaded is not None
    assert loaded.description == "A demo skill for testing."
    assert "## Steps" in loaded.body
    assert loaded.tags == ["demo", "test"]


def test_create_duplicate_requires_overwrite(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="dup", description="first", body=SAMPLE_BODY)
    with pytest.raises(SkillError):
        registry.create(name="dup", description="second", body=SAMPLE_BODY)

    replaced = registry.create(
        name="dup", description="second", body=SAMPLE_BODY, overwrite=True
    )
    assert replaced.description == "second"


def test_update_replaces_and_appends(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="u", description="d", body=SAMPLE_BODY)

    registry.update("u", body="## Steps\n1. Only step.\n")
    assert "Only step" in registry.get("u").body
    assert "Do the first part" not in registry.get("u").body

    registry.update("u", body="## Extra\nappended note\n", append=True)
    updated = registry.get("u").body
    assert "Only step" in updated
    assert "appended note" in updated


def test_update_missing_skill_raises(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    with pytest.raises(SkillError):
        registry.update("ghost", body="x")


def test_delete(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="temp", description="d", body=SAMPLE_BODY)
    assert registry.delete("temp") is True
    assert registry.get("temp") is None
    assert registry.delete("temp") is False


def test_search_matches_name_description_and_body(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="alpha", description="deploys websites", body=SAMPLE_BODY)
    registry.create(name="beta", description="runs tests", body="## Steps\n1. use kubernetes here\n")

    assert [s.name for s in registry.search("deploy")] == ["alpha"]
    assert [s.name for s in registry.search("kubernetes")] == ["beta"]
    assert registry.search("nonexistent-term") == []


def test_render_index_lists_all(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="one", description="first skill", body=SAMPLE_BODY)
    registry.create(name="two", description="second skill", body=SAMPLE_BODY)

    index = registry.render_index()
    assert "`one`" in index
    assert "`two`" in index
    assert "first skill" in index


def test_render_index_empty_when_no_skills(tmp_path: Path) -> None:
    assert SkillRegistry(tmp_path / "skills").render_index() == ""


def test_seeds_install_and_do_not_clobber(tmp_path: Path) -> None:
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "seed-a.md").write_text(
        "---\nname: seed-a\ndescription: from seeds\n---\n\nbody\n", encoding="utf-8"
    )

    skills_dir = tmp_path / "skills"
    registry = SkillRegistry(skills_dir, seeds_dir=seeds)

    installed = registry.install_seeds()
    assert installed == ["seed-a"]
    assert registry.get("seed-a").description == "from seeds"

    # A learned version must survive re-seeding.
    registry.update("seed-a", description="user improved this")
    registry.install_seeds()
    assert registry.get("seed-a").description == "user improved this"


def test_render_and_report(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="r", description="d", body=SAMPLE_BODY)
    assert "# Skill: r" in registry.get("r").render()
    assert "r" in registry.to_markdown_report()


# -- curator ---------------------------------------------------------------

class StubProvider:
    """Provider that replays one canned response for the curator."""

    def __init__(self, response) -> None:
        self.response = response
        self.calls = 0

    def chat(self, messages, tools=None, temperature=0.0, stream_callback=None):
        self.calls += 1
        return self.response


def test_curator_below_floor_skips(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    curator = SkillCurator(registry, StubProvider(None))
    outcome = curator.curate(task="t", steps=[{"tool": "shell"}], summary="s", min_steps=3)
    assert outcome.changed is False
    assert "below the curation floor" in outcome.reason


def test_curator_declines_when_model_says_none(tmp_path: Path, make_final) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    curator = SkillCurator(registry, StubProvider(make_final("NONE")))
    steps = [{"tool": "shell"} for _ in range(5)]
    outcome = curator.curate(task="t", steps=steps, summary="s", min_steps=3)
    assert outcome.changed is False
    assert "not worth saving" in outcome.reason


def test_curator_creates_skill_from_tool_call(tmp_path: Path, make_tool_call) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    proposal = make_tool_call(
        "propose_skill",
        {
            "name": "auto-learned-skill",
            "description": "Learned automatically from a session.",
            "tags": ["auto"],
            "body": SAMPLE_BODY,
        },
    )
    curator = SkillCurator(registry, StubProvider(proposal))
    steps = [{"tool": "shell", "arguments": {"command": "ls"}} for _ in range(4)]

    outcome = curator.curate(task="do a thing", steps=steps, summary="done", min_steps=3)
    assert outcome.created is True
    assert outcome.skill.name == "auto-learned-skill"
    assert registry.get("auto-learned-skill") is not None
    assert outcome.skill.source == "curator"


def test_curator_updates_instead_of_duplicating(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.create(name="existing", description="original", body=SAMPLE_BODY)

    curator = SkillCurator(registry, StubProvider(None))
    outcome = curator.commit(
        {
            "name": "existing",
            "description": "improved description",
            "body": "## Steps\n1. A brand new improved step.\n",
        }
    )
    assert outcome.updated is True
    assert outcome.created is False
    skill = registry.get("existing")
    assert skill.description == "improved description"
    assert len(registry.list()) == 1, "must not create a duplicate skill"


def test_curator_handles_provider_failure(tmp_path: Path) -> None:
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("provider exploded")

    registry = SkillRegistry(tmp_path / "skills")
    curator = SkillCurator(registry, Boom())
    steps = [{"tool": "shell"} for _ in range(4)]
    outcome = curator.curate(task="t", steps=steps, summary="s", min_steps=3)
    assert outcome.changed is False
    assert "curator model call failed" in outcome.reason


def test_render_transcript_includes_tool_and_args() -> None:
    transcript = SkillCurator.render_transcript(
        "the task",
        [{"tool": "shell", "arguments": {"command": "ls"}, "summary": "listed files"}],
    )
    assert "the task" in transcript
    assert "shell" in transcript
    assert "ls" in transcript
    assert "listed files" in transcript
