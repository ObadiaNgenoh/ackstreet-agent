"""Tests for the tool layer."""

from __future__ import annotations

from ackstreet.config import Config
from ackstreet.tools import build_default_registry
from ackstreet.tools.base import ToolResult
from ackstreet.tools.files import EditFileTool, ReadFileTool, SearchFilesTool, WriteFileTool
from ackstreet.tools.python_exec import PythonExecTool
from ackstreet.tools.shell import ShellTool

# -- registry --------------------------------------------------------------

def test_default_registry_registers_expected_tools(config: Config) -> None:
    registry = build_default_registry(config)
    names = registry.names()
    for expected in (
        "shell",
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "search_files",
        "web_search",
        "fetch_url",
        "python",
    ):
        assert expected in names, f"{expected} should be registered"


def test_disabled_tools_are_not_registered(config: Config) -> None:
    config.set("tools", "allow_shell", False)
    config.set("tools", "allow_web", False)
    registry = build_default_registry(config)
    assert "shell" not in registry.names()
    assert "web_search" not in registry.names()
    assert "read_file" in registry.names()


def test_unknown_tool_returns_failure_not_exception(config: Config) -> None:
    registry = build_default_registry(config)
    result = registry.execute("no_such_tool", {})
    assert result.ok is False
    assert "no tool named" in result.error


def test_schemas_are_wellformed(config: Config) -> None:
    registry = build_default_registry(config)
    for schema in registry.schemas():
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"
        # Every required key must exist in properties.
        for required in fn["parameters"].get("required", []):
            assert required in fn["parameters"]["properties"]


# -- shell -----------------------------------------------------------------

def test_shell_runs_and_captures_output(config: Config) -> None:
    tool = ShellTool(config)
    result = tool.run(command="echo hello-tools")
    assert result.ok
    assert "hello-tools" in result.output
    assert "exit_code: 0" in result.output


def test_shell_reports_nonzero_exit(config: Config) -> None:
    tool = ShellTool(config)
    result = tool.run(command="exit 3")
    assert result.ok is False
    assert "3" in result.error


def test_shell_blocks_dangerous_patterns(config: Config) -> None:
    tool = ShellTool(config)
    result = tool.run(command="rm -rf / --no-preserve-root")
    assert result.ok is False
    assert "blocked pattern" in result.error


def test_shell_timeout_is_enforced(config: Config) -> None:
    tool = ShellTool(config)
    result = tool.run(command="sleep 5", timeout=1)
    assert result.ok is False
    assert result.metadata.get("timed_out") is True


# -- files -----------------------------------------------------------------

def test_write_then_read_roundtrip(config: Config) -> None:
    write = WriteFileTool(config)
    read = ReadFileTool(config)

    written = write.run(path="notes/hello.txt", content="line one\nline two\n")
    assert written.ok
    assert (config.workspace / "notes" / "hello.txt").exists()

    loaded = read.run(path="notes/hello.txt")
    assert loaded.ok
    assert "line one" in loaded.output
    assert "line two" in loaded.output


def test_read_missing_file_fails_cleanly(config: Config) -> None:
    result = ReadFileTool(config).run(path="nope.txt")
    assert result.ok is False
    assert "does not exist" in result.error


def test_read_line_range(config: Config) -> None:
    WriteFileTool(config).run(path="many.txt", content="\n".join(f"line{i}" for i in range(1, 21)))
    result = ReadFileTool(config).run(path="many.txt", start_line=5, end_line=7)
    assert result.ok
    assert "line5" in result.output
    assert "line7" in result.output
    assert "line8" not in result.output


def test_edit_requires_unique_match(config: Config) -> None:
    WriteFileTool(config).run(path="dup.txt", content="alpha\nbeta\nalpha\n")
    edit = EditFileTool(config)

    ambiguous = edit.run(path="dup.txt", old_text="alpha", new_text="gamma")
    assert ambiguous.ok is False
    assert "appears 2 times" in ambiguous.error

    forced = edit.run(path="dup.txt", old_text="alpha", new_text="gamma", replace_all=True)
    assert forced.ok
    assert ReadFileTool(config).run(path="dup.txt").output.count("gamma") == 2


def test_edit_missing_text_fails(config: Config) -> None:
    WriteFileTool(config).run(path="x.txt", content="content\n")
    result = EditFileTool(config).run(path="x.txt", old_text="absent", new_text="new")
    assert result.ok is False
    assert "not found" in result.error


def test_search_files_finds_matches(config: Config) -> None:
    WriteFileTool(config).run(path="pkg/a.py", content="import os\nTODO: fix this\n")
    WriteFileTool(config).run(path="pkg/b.py", content="nothing here\n")
    result = SearchFilesTool(config).run(pattern="TODO", path="pkg")
    assert result.ok
    assert "a.py" in result.output
    assert "b.py" not in result.output


def test_list_directory_reports_entries(config: Config) -> None:
    WriteFileTool(config).run(path="d/one.txt", content="1")
    WriteFileTool(config).run(path="d/two.txt", content="2")
    result = config and build_default_registry(config).execute("list_directory", {"path": "d"})
    assert result.ok
    assert "one.txt" in result.output
    assert "two.txt" in result.output


# -- python ----------------------------------------------------------------

def test_python_executes_and_captures_stdout(config: Config) -> None:
    result = PythonExecTool(config).run(code="print(2 + 2)")
    assert result.ok
    assert "4" in result.output


def test_python_reports_exceptions(config: Config) -> None:
    result = PythonExecTool(config).run(code="raise ValueError('boom')")
    assert result.ok is False
    assert "ValueError" in result.error


def test_python_state_does_not_leak_between_calls(config: Config) -> None:
    tool = PythonExecTool(config)
    tool.run(code="x = 42")
    result = tool.run(code="print('x' in dir())")
    assert "False" in result.output


# -- validation ------------------------------------------------------------

def test_missing_required_argument_is_reported(config: Config) -> None:
    registry = build_default_registry(config)
    result = registry.execute("read_file", {})
    assert result.ok is False
    assert "missing required argument" in result.error


def test_malformed_json_arguments_are_reported(config: Config) -> None:
    registry = build_default_registry(config)
    result = registry.execute("shell", {"__parse_error__": "invalid JSON", "__raw__": "{"})
    assert result.ok is False
    assert "not valid JSON" in result.error


def test_output_truncation(config: Config) -> None:
    result = ToolResult.success("x" * 5000)
    rendered = result.render(limit=1000)
    assert len(rendered) < 1200
    assert "truncated" in rendered
