#!/usr/bin/env bash
# End-to-end proof that a chat-platform message reaches the real agent loop and
# that the approval gate works over a chat channel.
#
# What is REAL here:
#   * the real TelegramConnector (update parsing, long-poll loop, send)
#   * the real MessageRouter, SessionManager and approval-over-chat flow
#   * the real Agent, ToolRegistry, shell/write_file tools and SafetyPolicy
#   * a real local HTTP server speaking the OpenAI /chat/completions schema
#
# What is NOT real:
#   * api.telegram.org -- a local HTTP server answers the Bot API calls instead
#   * the model -- the local OpenAI-compatible server replies from a script
#
# Usage:  scripts/connector_e2e.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
HOST="127.0.0.1"
PORT="18474"
EXPORT_DIR="$(mktemp -d)"

pass() { printf '  PASS  %s\n' "$1"; PASSED=$((PASSED + 1)); }
fail() { printf '  FAIL  %s\n' "$1"; FAILED=$((FAILED + 1)); }

PASSED=0
FAILED=0

cleanup() {
  [ -n "${TG_PID:-}" ] && kill "$TG_PID" 2>/dev/null
  wait 2>/dev/null
}
trap cleanup EXIT

# --- fake Bot API -----------------------------------------------------------
cat > "$EXPORT_DIR/fake_telegram.py" <<'PY'
"""A tiny Bot API stand-in.

It serves the queued task on the first poll, but deliberately WITHHOLDS the
user's "yes" reply until the approval prompt has actually been sent -- which is
what a real person would do: you cannot answer a question that has not arrived
yet. That makes the run race-free rather than timing-dependent.
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

USER_ID = 42
CHAT_ID = 100

TASK = {"update_id": 1, "message": {"message_id": 11,
    "from": {"id": USER_ID, "first_name": "Ada"},
    "chat": {"id": CHAT_ID, "type": "private"},
    "text": "write a file called hello.txt containing the word ackstreet, then say done"}}

ANSWER = {"update_id": 2, "message": {"message_id": 12,
    "from": {"id": USER_ID, "first_name": "Ada"},
    "chat": {"id": CHAT_ID, "type": "private"},
    "text": "yes"}}

SENT = []
QUEUE = [TASK]
PROMPTED = False
ANSWER_SERVED = [False]


def next_update():
    """Serve the task first, then the answer once the prompt has gone out."""
    if QUEUE:
        return QUEUE.pop(0)
    if PROMPTED and not ANSWER_SERVED[0]:
        ANSWER_SERVED[0] = True
        return ANSWER
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global PROMPTED
        method = self.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return self._send({"ok": True, "result": {"id": 1, "username": "e2e_bot", "is_bot": True}})
        if method == "getUpdates":
            update = next_update()
            return self._send({"ok": True, "result": [update] if update else []})
        if method == "sendMessage":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            text = data.get("text", "")
            SENT.append(text)
            if "Approval needed" in text:
                PROMPTED = True
            with open(sys.argv[2], "w") as fh:
                json.dump(SENT, fh)
            return self._send({"ok": True, "result": {"message_id": len(SENT)}})
        return self._send({"ok": True, "result": True})


if __name__ == "__main__":
    HTTPServer((sys.argv[1], int(sys.argv[3])), Handler).serve_forever()
PY

"$PY" "$EXPORT_DIR/fake_telegram.py" "$HOST" "$EXPORT_DIR/sent.json" "$PORT" &
TG_PID=$!
sleep 1.2

echo "==> Running the connector end to end"
echo "    (local Bot API + local OpenAI-compatible model; no external network)"
echo
export ACKSTREET_HOME="$EXPORT_DIR/home"
export ACKSTREET_TELEGRAM_BOT_TOKEN="111:fake-token"
export ACKSTREET_TELEGRAM_API_BASE="http://$HOST:$PORT"
export ACKSTREET_E2E_SENT="$EXPORT_DIR/sent.json"
export ACKSTREET_E2E_WORKSPACE="$EXPORT_DIR/workspace"
export PYTHONPATH="$ROOT"

"$PY" "$ROOT/scripts/connector_e2e_driver.py" "$ROOT" 2>&1 | sed 's/^/  /'
RC=${PIPESTATUS[0]}

echo
echo "==> Checks"
[ "$RC" -eq 0 ] && pass "connector run exited cleanly" || fail "connector run failed (rc=$RC)"

if grep -q ackstreet "$EXPORT_DIR/home/workspace/hello.txt" 2>/dev/null; then
  pass "agent wrote the file through the shell/file tool"
else
  fail "workspace file was not written"
fi

if grep -q "Approval needed" "$EXPORT_DIR/sent.json" 2>/dev/null; then
  pass "approval prompt was delivered as a chat message"
else
  fail "no approval prompt in the chat transcript"
fi

if grep -q "Approved" "$EXPORT_DIR/sent.json" 2>/dev/null; then
  pass "in-chat 'yes' reply was applied to the gate"
else
  fail "the approval reply was not honoured"
fi

echo
echo "==> Chat transcript (what the user would see)"
"$PY" -c "
import json, sys
try:
    sent = json.load(open('$EXPORT_DIR/sent.json'))
except Exception:
    sent = []
for i, text in enumerate(sent, 1):
    print(f'  [{i}] {text[:110]}')
"

echo
if [ "$FAILED" -eq 0 ]; then
  echo "==> ALL CHECKS PASSED ($PASSED)"
else
  echo "==> $FAILED CHECK(S) FAILED"
fi
exit "$FAILED"
