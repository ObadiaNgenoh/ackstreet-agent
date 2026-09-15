"""Driver for scripts/connector_e2e.sh.

Wires the real connector, router and agent to a local fake Bot API and a local
OpenAI-compatible model server, then feeds one task through and lets the
approval gate ask for permission in the chat.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = sys.argv[1]
sys.path.insert(0, ROOT)

from ackstreet.agent import Agent  # noqa: E402
from ackstreet.config import Config  # noqa: E402
from ackstreet.connectors.router import MessageRouter  # noqa: E402
from ackstreet.connectors.telegram import (  # noqa: E402
    HttpTelegramTransport,
    TelegramConnector,
)

MODEL_PORT = 18475


# ---------------------------------------------------------------------------
# A local OpenAI-compatible model that scripts two turns: ask for a file write,
# then finish. This exercises real HTTP + real JSON + the real provider client.
# ---------------------------------------------------------------------------

SCRIPT = [
    {
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps(
                        {"path": "hello.txt", "content": "ackstreet says hello"}
                    ),
                },
            }
        ]
    },
    {"content": "Done: I wrote hello.txt with the word ackstreet in it."},
]

TURNS = {"n": 0}


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        index = min(TURNS["n"], len(SCRIPT) - 1)
        TURNS["n"] += 1
        turn = SCRIPT[index]

        message = {"role": "assistant", "content": turn.get("content")}
        finish = "stop"
        if "tool_calls" in turn:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": turn["tool_calls"],
            }
            finish = "tool_calls"

        body = json.dumps(
            {
                "id": "chatcmpl-e2e",
                "object": "chat.completion",
                "model": "e2e-model",
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_model() -> str:
    server = HTTPServer(("127.0.0.1", MODEL_PORT), ModelHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{MODEL_PORT}/v1"


# ---------------------------------------------------------------------------
# Wire it up
# ---------------------------------------------------------------------------

def main() -> int:
    base = start_model()

    cfg = Config.load()
    cfg.ensure_dirs()
    cfg.set("agent", "provider", "openai")
    cfg.set("agent", "model", "e2e-model")
    cfg.set("agent", "base_url", base)
    cfg.set("agent", "api_key", "sk-e2e-local")
    cfg.set("agent", "approval_mode", "ask")
    cfg.set("connectors", "approval_timeout", 30)
    cfg.set("connectors", "allowed_user_ids", ["42"])
    cfg.save()
    os.environ["OPENAI_API_KEY"] = "sk-e2e-local"
    os.environ["OPENAI_BASE_URL"] = base

    print(f"workspace   {cfg.workspace}")
    print(f"model       {base} (scripted)")
    print("approval    ask (answered in-chat)")
    print()

    transport = HttpTelegramTransport(
        "111:fake-token", base_url=os.environ["ACKSTREET_TELEGRAM_API_BASE"]
    )
    connector = TelegramConnector(cfg, transport=transport)
    print(f"telegram    {connector.get_me().get('username')}")

    router = MessageRouter(
        cfg, connector, agent_factory=lambda _k: Agent(cfg), log=lambda m: print(f"  [log] {m}")
    )

    stop = threading.Event()

    # Feed the queued updates through the real listener on worker threads, so
    # the in-chat "yes" can be read while the first turn waits at the gate.
    def on_message(incoming) -> None:
        future = router.dispatch_async(incoming)
        print(f"  queued {incoming.text[:60]!r}")

        def report(f=future, text=incoming.text) -> None:
            try:
                outcome = f.result(timeout=60)
            except Exception as exc:  # noqa: BLE001
                print(f"  {text[:40]!r} -> ERROR {exc}")
                return
            print(
                f"  routed {text[:50]!r} -> handled={outcome.handled} "
                f"steps={outcome.steps} reason={outcome.reason}"
            )
            if outcome.reason == "handled":
                stop.set()

        threading.Thread(target=report, daemon=True).start()

    connector.listen(on_message=on_message, stop_event=stop)
    stop.set()
    router.wait_for_idle(timeout=60)

    # Allow the in-flight agent turn to finish before reporting.
    time.sleep(1.5)

    path = os.path.join(str(cfg.workspace), "hello.txt")
    print()
    print(f"file written: {os.path.exists(path)}")
    if os.path.exists(path):
        with open(path) as fh:
            print(f"file content: {fh.read()!r}")
    print(f"sessions:     {router.sessions.count()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
