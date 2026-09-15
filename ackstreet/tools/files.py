"""File system tools: read, write, edit, list, delete, search."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any, List

from .base import Tool, ToolResult


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n... [{len(text) - limit} characters omitted] ...\n{tail}"


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a text file and return its contents. Always read a file before editing it. "
        "Use start_line/end_line to read a slice of a large file instead of the whole thing."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path (relative paths resolve inside the workspace)."},
            "start_line": {"type": "integer", "description": "1-based first line to return (optional)."},
            "end_line": {"type": "integer", "description": "1-based last line to return (optional)."},
        },
        "required": ["path"],
    }

    def run(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        **_: Any,
    ) -> ToolResult:
        target = self.resolve_path(path)
        if not target.exists():
            return ToolResult.failure(f"file does not exist: {target}")
        if target.is_dir():
            return ToolResult.failure(f"{target} is a directory — use list_directory")

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult.failure(f"could not read {target}: {exc}")

        total_lines = text.count("\n") + (1 if text else 0)

        if start_line is not None or end_line is not None:
            lines = text.splitlines()
            start = max(1, int(start_line or 1))
            end = min(len(lines), int(end_line or len(lines)))
            if start > end:
                return ToolResult.failure(f"invalid range: start_line {start} > end_line {end}")
            text = "\n".join(lines[start - 1 : end])
            header = f"{target} (lines {start}-{end} of {total_lines})"
        else:
            header = f"{target} ({total_lines} lines)"

        return ToolResult.success(
            f"{header}\n---\n{_truncate(text, self.output_limit)}",
            path=str(target),
            lines=total_lines,
        )


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a file or overwrite it completely. Parent directories are created automatically. "
        "To change part of an existing file, use edit_file instead of rewriting the whole thing."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    }
    dangerous = True

    def run(self, path: str, content: str, **_: Any) -> ToolResult:
        target = self.resolve_path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"could not write {target}: {exc}")

        verb = "overwrote" if existed else "created"
        return ToolResult.success(
            f"{verb} {target} ({len(content)} characters, "
            f"{content.count(chr(10)) + 1} lines)",
            path=str(target),
            bytes=len(content.encode("utf-8")),
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string inside a file. old_text must match the file byte-for-byte "
        "and appear exactly once (read the file first). Use this for targeted edits instead of "
        "rewriting a whole file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "old_text": {"type": "string", "description": "Exact existing text to replace."},
            "new_text": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring uniqueness."},
        },
        "required": ["path", "old_text", "new_text"],
    }
    dangerous = True

    def run(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **_: Any,
    ) -> ToolResult:
        target = self.resolve_path(path)
        if not target.exists():
            return ToolResult.failure(f"file does not exist: {target}")

        try:
            original = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"could not read {target}: {exc}")

        occurrences = original.count(old_text)
        if occurrences == 0:
            return ToolResult.failure(
                f"old_text was not found in {target}. Read the file and copy the "
                "text exactly, including indentation."
            )
        if occurrences > 1 and not replace_all:
            return ToolResult.failure(
                f"old_text appears {occurrences} times in {target}. Include more "
                "surrounding context to make it unique, or set replace_all=true."
            )

        updated = original.replace(old_text, new_text) if replace_all else original.replace(
            old_text, new_text, 1
        )
        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"could not write {target}: {exc}")

        replaced = occurrences if replace_all else 1
        return ToolResult.success(
            f"edited {target}: replaced {replaced} occurrence(s)",
            path=str(target),
            replacements=replaced,
        )


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = "List files and folders in a directory, with sizes. Use to orient yourself before reading files."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path. Defaults to the workspace."},
            "recursive": {"type": "boolean", "description": "Walk subdirectories (default false)."},
        },
        "required": [],
    }

    def run(self, path: str | None = None, recursive: bool = False, **_: Any) -> ToolResult:
        target = self.resolve_path(path, default=self.config.workspace)
        if not target.exists():
            return ToolResult.failure(f"directory does not exist: {target}")
        if not target.is_dir():
            return ToolResult.failure(f"{target} is a file — use read_file")

        lines: List[str] = [f"{target}"]
        count = 0
        try:
            if recursive:
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".venv"}]
                    rel_root = Path(root).relative_to(target)
                    for name in sorted(files):
                        entry = Path(root) / name
                        count += 1
                        if count > 400:
                            lines.append("... (listing truncated at 400 entries)")
                            return ToolResult.success("\n".join(lines), truncated=True)
                        lines.append(f"  {rel_root / name}  ({entry.stat().st_size} bytes)")
            else:
                for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
                    count += 1
                    kind = "dir " if entry.is_dir() else "file"
                    size = "" if entry.is_dir() else f"  {entry.stat().st_size} bytes"
                    lines.append(f"  [{kind}] {entry.name}{size}")
        except OSError as exc:
            return ToolResult.failure(f"could not list {target}: {exc}")

        lines.append(f"({count} entries)")
        return ToolResult.success("\n".join(lines), path=str(target), entries=count)


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Delete a file, or a directory and its contents. This is irreversible — "
        "only use it when the task explicitly requires removal."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory to delete."},
            "recursive": {"type": "boolean", "description": "Required to delete a non-empty directory."},
        },
        "required": ["path"],
    }
    dangerous = True

    def run(self, path: str, recursive: bool = False, **_: Any) -> ToolResult:
        target = self.resolve_path(path)
        if not target.exists():
            return ToolResult.success(f"{target} did not exist; nothing to delete")

        try:
            if target.is_dir():
                if any(target.iterdir()) and not recursive:
                    return ToolResult.failure(
                        f"{target} is a non-empty directory — pass recursive=true to remove it"
                    )
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            return ToolResult.failure(f"could not delete {target}: {exc}")
        return ToolResult.success(f"deleted {target}", path=str(target))


class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Search file contents for a regular expression, returning matching lines with "
        "file names and line numbers. Use to locate code before reading it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression to search for."},
            "path": {"type": "string", "description": "Directory to search. Defaults to the workspace."},
            "glob": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py'."},
            "max_results": {"type": "integer", "description": "Stop after this many matches (default 50)."},
        },
        "required": ["pattern"],
    }

    SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", "dist", "build"}

    def run(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        max_results: int = 50,
        **_: Any,
    ) -> ToolResult:
        root = self.resolve_path(path, default=self.config.workspace)
        if not root.exists():
            return ToolResult.failure(f"path does not exist: {root}")

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult.failure(f"invalid regular expression: {exc}")

        matches: List[str] = []
        files_scanned = 0

        candidates: Iterable[Path] = [root] if root.is_file() else root.rglob(glob or "*")
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if any(part in self.SKIP for part in candidate.parts):
                continue
            files_scanned += 1
            try:
                with open(candidate, encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if regex.search(line):
                            try:
                                display = candidate.relative_to(root)
                            except ValueError:
                                display = candidate
                            matches.append(f"{display}:{line_number}: {line.rstrip()[:200]}")
                            if len(matches) >= max_results:
                                raise StopIteration
            except StopIteration:
                break
            except OSError:
                continue

        if not matches:
            return ToolResult.success(
                f"no matches for /{pattern}/ ({files_scanned} files scanned)", matches=0
            )
        header = f"{len(matches)} match(es) for /{pattern}/ in {root}:"
        return ToolResult.success("\n".join([header] + matches), matches=len(matches))


__all__ = [
    "DeleteFileTool",
    "EditFileTool",
    "ListDirectoryTool",
    "ReadFileTool",
    "SearchFilesTool",
    "WriteFileTool",
]
