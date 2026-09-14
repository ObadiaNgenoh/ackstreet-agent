#!/usr/bin/env python3
"""A tiny OpenAI-compatible server used to test ACKSTREET AGENT without a key.

It speaks just enough of the ``/chat/completions`` schema to drive a real agent
loop: it looks at the conversation it receives and replies with a scripted
sequence of tool calls, then a final answer.

Run it:

    python scripts/mock_openai_server.py --port 8099

Then point the agent at it:

    export ACKSTREET_HOME=/tmp/ackstreet-e2e
    export MOCK_API_KEY=test
    ackstreet config set providers.openai.base_url http://127.0.0.1:8099/v1
    ackstreet config set providers.openai.api_key_env MOCK_API_KEY
    ackstreet config set providers.openai.model mock-model
    ackstreet run "prove the loop works"

This is a development harness. It does not implement streaming, and it is not
intended to be exposed to a network.
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

# The scripted behaviour: one tool call per assistant turn, in order, followed
# by a final prose answer. Each entry is (tool_name, arguments).
SCRIPT: List[Dict[str, Any]] = [
    {
        "tool": "shell",
        "arguments": {
            "command": "echo ackstreet-e2e-ok && pwd && uname -s",
        },
    },
    {
        "tool": "write_file",
        "arguments": {
            "path": "e2e-proof.txt",
            "content": (
                "ACKSTREET AGENT end-to-end proof\n"
                "This file was written by the agent through its own write_file tool.\n"
                "If you can read this, the tool-calling loop worked.\n"
            ),
        },
    },
    {
        "tool": "python",
        "arguments": {
            "code": "print('sum:', sum(range(1, 101)))",
        },
    },
    {
        "tool": "save_skill",
        "arguments": {
            "name": "e2e-prove-tool-loop",
            "description": (
                "Prove the agent tool-calling loop works end to end: run a shell "
                "command, write a file, execute python, then save a skill."
            ),
            "tags": ["testing", "verification"],
            "body": (
                "## When to Use\n"
                "When you need to confirm the agent's tool loop, filesystem access "
                "and skill persistence are all working after an install.\n\n"
                "## Steps\n"
                "1. Run `echo ackstreet-e2e-ok` through the shell tool.\n"
                "2. Write a marker file with write_file and read it back.\n"
                "3. Execute a short Python snippet with the python tool.\n"
                "4. Save this skill so it loads in future sessions.\n\n"
                "## Pitfalls\n"
                "- Assuming a write succeeded without reading the file back.\n"
                "- Forgetting that the python tool runs in a fresh interpreter.\n\n"
                "## Verification\n"
                "- The skill file exists on disk under the skills directory.\n"
                "- The marker file is present in the workspace.\n"
            ),
        },
    },
]

FINAL_TEXT = (
    "End-to-end loop verified. I ran a shell command, wrote e2e-proof.txt through "
    "the write_file tool, executed a Python snippet, and saved a reusable skill to "
    "disk. All four tool calls returned successfully."
)


def count_assistant_turns(messages: List[Dict[str, Any]]) -> int:
    """How many tool-calling turns the model has already taken."""
    return sum(1 for m in messages if m.get("role") == "assistant")


def build_response(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    turn = count_assistant_turns(messages)

    if turn < len(SCRIPT):
        step = SCRIPT[turn]
        return {
            "id": f"mock-{turn}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"call_{turn}",
                                "type": "function",
                                "function": {
                                    "name": step["tool"],
                                    "arguments": json.dumps(step["arguments"]),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    return {
        "id": "mock-final",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": FINAL_TEXT},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
    }


class MockHandler(BaseHTTPRequestHandler):
    server_version = "AckstreetMock/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send_json(
                {
                    "object": "list",
                    "data": [{"id": "mock-model", "object": "model", "owned_by": "mock"}],
                }
            )
            return
        if self.path.rstrip("/").endswith("/health"):
            self._send_json({"status": "ok"})
            return
        self._send_json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": {"message": "invalid JSON body"}}, status=400)
            return

        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json({"error": {"message": "not found"}}, status=404)
            return

        messages = payload.get("messages") or []
        self._send_json(build_response(messages))


def serve(port: int, host: str, verbose: bool) -> None:
    httpd = ThreadingHTTPServer((host, port), MockHandler)
    httpd.verbose = verbose  # type: ignore[attr-defined]
    print(f"mock OpenAI-compatible server listening on http://{host}:{port}/v1")
    print(f"  chat endpoint:  http://{host}:{port}/v1/chat/completions")
    print(f"  models:         http://{host}:{port}/v1/models")
    print("  Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock OpenAI-compatible server for tests")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    serve(args.port, args.host, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
