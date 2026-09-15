#!/usr/bin/env bash
# Manual proof that the approval gate works through the real CLI.
#
# The bundled mock server replays a fixed script (shell -> write_file
# e2e-proof.txt -> python -> save_skill), so the file we watch for is
# e2e-proof.txt. What changes between the tests below is only whether the gate
# lets that write_file call through.
set -u

# Resolve the checkout and the entry points the same way scripts/e2e_demo.sh
# does, so this works from a clone with a .venv and from an installed package.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -n "${VENV_PY:-}" ] && [ -x "$VENV_PY" ]; then
  PY="$VENV_PY"
elif [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PY="$REPO_DIR/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python || true)"
fi

if [ -x "$REPO_DIR/.venv/bin/ackstreet" ]; then
  ACK="$REPO_DIR/.venv/bin/ackstreet"
else
  ACK="${ACKSTREET_BIN:-$(command -v ackstreet || true)}"
fi

if [ -z "${PY:-}" ] || [ -z "${ACK:-}" ]; then
  echo "could not find python and/or ackstreet." >&2
  echo "Set VENV_PY=/path/to/python and ACKSTREET_BIN=/path/to/ackstreet." >&2
  exit 1
fi

HOME_DIR=/tmp/ack-approval/home
WS="$HOME_DIR/workspace"
TARGET="$WS/e2e-proof.txt"

rm -rf /tmp/ack-approval
mkdir -p /tmp/ack-approval

"$PY" "$REPO_DIR/scripts/mock_openai_server.py" --port 8097 >/tmp/ack-approval/mock.log 2>&1 &
MOCK_PID=$!
sleep 3

export ACKSTREET_HOME="$HOME_DIR"
export OPENAI_API_KEY=test-key
export OPENAI_BASE_URL=http://127.0.0.1:8097/v1
export OPENAI_MODEL=mock-model

"$ACK" init >/dev/null 2>&1

reset_state() {
  rm -f "$TARGET"
  rm -rf "$HOME_DIR/skills"
}

check() {
  # $1 = expected (written|absent), $2 = label
  if [ -f "$TARGET" ]; then
    actual=written
  else
    actual=absent
  fi
  if [ "$actual" = "$1" ]; then
    echo "RESULT: file $actual  <-- CORRECT ($2)"
  else
    echo "RESULT: file $actual  <-- WRONG, expected $1 ($2)"
  fi
}

echo "############ TEST 1: --approval-mode ask, non-interactive -> must REFUSE ############"
reset_state
"$ACK" --approval-mode ask run "write a file" 2>&1 | grep -E "failed|refused|tool call" | head -4
check absent "the gate refused the write"

echo
echo "############ TEST 2: same task with --yes -> must RUN ############"
reset_state
"$ACK" --approval-mode ask run "write a file" --yes 2>&1 | grep -E "\[ok\]|tool call" | head -4
check written "--yes bypassed the gate"

echo
echo "############ TEST 3: denylist refuses even under --yes ############"
reset_state
"$ACK" config set agent.approval_denylist '["write_file"]' >/dev/null 2>&1
"$ACK" run "write a file" --yes 2>&1 | grep -E "failed|refused|tool call" | head -4
check absent "the denylist held despite --yes"

echo
echo "############ TEST 4: allowlist mode lets a matching call through ############"
reset_state
"$ACK" config set agent.approval_denylist '[]' >/dev/null 2>&1
"$ACK" config set agent.approval_mode allowlist >/dev/null 2>&1
"$ACK" config set agent.approval_allowlist '["write_file:*.txt"]' >/dev/null 2>&1
"$ACK" run "write a file" 2>&1 | grep -E "\[ok\]|failed|tool call" | head -4
check written "the allowlist rule matched *.txt"

echo
echo "############ TEST 5: allowlist mode blocks a non-matching call ############"
reset_state
"$ACK" config set agent.approval_allowlist '["write_file:*.md"]' >/dev/null 2>&1
"$ACK" run "write a file" 2>&1 | grep -E "failed|refused|tool call" | head -4
check absent "*.md did not match the .txt target"

echo
echo "############ doctor: approval section ############"
"$ACK" doctor 2>&1 | sed -n '/Approval gate/,/Skills and memory/p'

kill "$MOCK_PID" 2>/dev/null
wait "$MOCK_PID" 2>/dev/null
echo
echo "=== APPROVAL GATE PROOF COMPLETE ==="
