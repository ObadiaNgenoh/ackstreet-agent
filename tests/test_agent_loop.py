"""Tests for the agent loop: tool calling, guards, memory and curation."""

from __future__ import annotations

from conftest import ScriptedProvider, final, tool_call

from ackstreet.agent import Agent
from ackstreet.config import Config
from ackstreet.memory import MemoryStore
from ackstreet.skills.registry import SkillRegistry

SAMPLE_BODY = """\
## When to Use
When testing the loop.

## Steps
1. Call a tool.
2. Observe the result.

## Pitfalls
- Not checking the result.

## Verification
- The tool ran.
"""


def make_agent(config: Config, script) -> tuple[Agent, ScriptedProvider]:
    provider = ScriptedProvider(script)
    agent = Agent(config, provider=provider, on_event=None)
    # Curation would consume a scripted response; tests opt in explicitly.
    agent.config.set("agent", "auto_curate", False)
    return agent, provider


def test_single_tool_call_then_answer(config: Config) -> None:
    agent, provider = make_agent(
        config,
        [
            tool_call("write_file", {"path": "out.txt", "content": "hello from the loop\n"}),
            final("Done: wrote out.txt."),
        ],
    )

    result = agent.run("write out.txt", max_steps=5)

    assert result.text == "Done: wrote out.txt."
    assert len(result.steps) == 1
    assert result.steps[0]["tool"] == "write_file"
    assert result.steps[0]["ok"] is True
    assert (config.workspace / "out.txt").read_text(encoding="utf-8") == "hello from the loop\n"
    assert result.stopped_reason == "completed"


def test_multi_step_loop_chains_tools(config: Config) -> None:
    agent, _ = make_agent(
        config,
        [
            tool_call("shell", {"command": "echo first"}, call_id="c1"),
            tool_call("write_file", {"path": "chain.txt", "content": "second"}, call_id="c2"),
            tool_call("read_file", {"path": "chain.txt"}, call_id="c3"),
            final("Read back what was written."),
        ],
    )

    result = agent.run("chain three tools", max_steps=8)

    assert [s["tool"] for s in result.steps] == ["shell", "write_file", "read_file"]
    assert all(s["ok"] for s in result.steps)
    assert result.text == "Read back what was written."


def test_tool_results_are_fed_back_to_the_model(config: Config) -> None:
    agent, provider = make_agent(
        config,
        [
            tool_call("shell", {"command": "echo marker-xyz"}),
            final("saw the marker"),
        ],
    )
    agent.run("run echo", max_steps=4)

    # The last request must contain the tool's output as a tool-role message.
    last_request = provider.calls[-1]
    tool_messages = [m for m in last_request if m.role == "tool"]
    assert tool_messages, "tool result must be sent back to the provider"
    assert "marker-xyz" in tool_messages[0].content


def test_step_budget_is_enforced(config: Config) -> None:
    # A script that never terminates: always asks for another tool.
    script = [tool_call("shell", {"command": f"echo {i}"}, call_id=f"c{i}") for i in range(20)]
    agent, _ = make_agent(config, script)

    result = agent.run("loop forever", max_steps=3)
    assert result.stopped_reason == "step_budget_exhausted"
    assert len(result.steps) == 3


def test_repeat_detection_breaks_the_loop(config: Config) -> None:
    identical = tool_call("shell", {"command": "echo same"}, call_id="c1")
    script = [identical.model_copy() if hasattr(identical, "model_copy") else identical for _ in range(6)]
    # Build fresh identical responses (the scripted provider pops them).
    script = [
        tool_call("shell", {"command": "echo same"}, call_id=f"c{i}") for i in range(6)
    ]
    agent, _ = make_agent(config, script)

    result = agent.run("repeat yourself", max_steps=6)

    assert result.stopped_reason == "repeat_detected"
    # The repeat guard fires from the Nth identical call onward.
    failed = [s for s in result.steps if not s["ok"]]
    assert failed, "at least one call must be marked as a detected repeat"
    assert len(result.steps) < 6, "the guard must cut the loop short"


def test_malformed_tool_arguments_do_not_crash(config: Config) -> None:
    bad = tool_call("read_file", {"__parse_error__": "invalid JSON", "__raw__": "{"})
    agent, _ = make_agent(config, [bad, final("recovered")])

    result = agent.run("malformed args", max_steps=4)
    assert result.text == "recovered"
    assert result.steps[0]["ok"] is False


def test_unknown_tool_is_reported_to_model(config: Config) -> None:
    agent, _ = make_agent(
        config, [tool_call("telepathy", {"thought": "hi"}), final("no such tool")]
    )
    result = agent.run("use a fake tool", max_steps=4)
    assert result.steps[0]["ok"] is False
    assert "no tool named" in result.steps[0]["summary"]


def test_session_is_persisted_to_memory(config: Config) -> None:
    agent, _ = make_agent(
        config,
        [
            tool_call("shell", {"command": "echo persisted"}),
            final("Session summary text."),
        ],
    )
    result = agent.run("persist me", max_steps=4)

    store = MemoryStore(config.memory_dir)
    sessions = store.sessions()
    assert sessions, "session must be recorded in the memory index"
    assert sessions[0]["id"] == result.session_id
    assert "persist me" in sessions[0]["task"]

    stored = store.load_session(result.session_id)
    assert stored is not None
    assert "Session summary text." in stored.summary


def test_memory_recall_finds_previous_session(config: Config) -> None:
    store = MemoryStore(config.memory_dir)
    store.save_session(store.new_session("deploy the kubernetes ingress controller"))
    store.sessions()  # ensure the index is written

    block = store.recall("kubernetes ingress", limit=5)
    assert "kubernetes" in block.lower()


def test_memory_facts_roundtrip(config: Config) -> None:
    store = MemoryStore(config.memory_dir)
    store.remember("The staging cluster runs on port 8443", tags=["k8s"])

    facts = store.facts()
    assert len(facts) == 1
    assert "8443" in facts[0]["fact"]

    assert store.forget(0) is True
    assert store.facts() == []
    assert store.forget(0) is False


def test_system_prompt_includes_skills_index(config: Config) -> None:
    registry = SkillRegistry(config.skills_dir)
    registry.create(name="known-skill", description="a skill the agent should see", body=SAMPLE_BODY)
    agent = Agent(config, provider=ScriptedProvider([final("ok")]), skills=registry)

    prompt = agent.build_system_prompt("anything")
    assert "known-skill" in prompt
    assert "Skills you have learned" in prompt


def test_plan_parsing(config: Config) -> None:
    agent, _ = make_agent(
        config,
        [
            final(
                "1. Inspect the directory\n"
                "2. Write the report\n"
                "3. Verify the file exists\n"
            )
        ],
    )
    steps = agent.plan("do a thing")
    assert steps == ["Inspect the directory", "Write the report", "Verify the file exists"]


def test_auto_curation_saves_a_skill(config: Config, make_tool_call) -> None:
    provider = ScriptedProvider(
        [
            tool_call("shell", {"command": "echo a"}, call_id="c1"),
            tool_call("shell", {"command": "echo b"}, call_id="c2"),
            tool_call("shell", {"command": "echo c"}, call_id="c3"),
            final("Finished the repeatable task."),
            # The curator's response, consumed after the main loop:
            tool_call(
                "propose_skill",
                {
                    "name": "auto-saved-by-loop",
                    "description": "Saved automatically at the end of a run.",
                    "body": SAMPLE_BODY,
                    "tags": ["auto"],
                },
                call_id="curate1",
            ),
        ]
    )
    agent = Agent(config, provider=provider)
    agent.config.set("agent", "auto_curate", True)
    agent.config.set("skills", "min_steps_to_curate", 3)

    result = agent.run("a three step task", max_steps=6)

    assert "auto-saved-by-loop" in result.skills_learned
    assert agent.skills.get("auto-saved-by-loop") is not None
    # And it is now visible to future sessions.
    assert "auto-saved-by-loop" in agent.build_system_prompt("later task")


def test_chat_turn_keeps_history(config: Config) -> None:
    provider = ScriptedProvider(
        [
            tool_call("shell", {"command": "echo turn-one"}),
            final("first reply"),
            final("second reply"),
        ]
    )
    agent = Agent(config, provider=provider)
    agent.config.set("agent", "auto_curate", False)

    first = agent.chat_turn("first message", stream=False)
    assert first.text == "first reply"

    second = agent.chat_turn("second message", stream=False)
    assert second.text == "second reply"

    # History accumulates across turns.
    assert any("first message" in m.content for m in agent.messages)
    assert any("second message" in m.content for m in agent.messages)


def test_reset_clears_conversation_but_not_skills(config: Config) -> None:
    registry = SkillRegistry(config.skills_dir)
    registry.create(name="persistent", description="d", body=SAMPLE_BODY)
    agent = Agent(config, provider=ScriptedProvider([final("hi")]), skills=registry)

    agent.chat_turn("hello", stream=False)
    assert agent.messages

    agent.reset()
    assert agent.messages == []
    assert agent.skills.get("persistent") is not None


def test_context_trimming_keeps_recent_messages(config: Config) -> None:
    agent, _ = make_agent(config, [final("ok")])
    agent.messages = [agent.messages.append] and []
    agent.config.set("agent", "context_messages", 4)

    from ackstreet.providers.base import Message

    agent.messages = [Message(role="system", content="sys")]
    for index in range(20):
        agent.messages.append(Message(role="user", content=f"message-{index}"))

    trimmed = agent._trim_context(agent.messages, keep=4)
    # System prompt always survives.
    assert trimmed[0].role == "system"
    # The most recent messages survive.
    assert "message-19" in trimmed[-1].content
    # Something was dropped and announced.
    assert any("trimmed" in m.content for m in trimmed if m.role == "user")
