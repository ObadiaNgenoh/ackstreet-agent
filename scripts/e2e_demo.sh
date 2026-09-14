#!/usr/bin/env bash
#
# End-to-end proof for ACKSTREET AGENT.
#
# Starts the bundled mock OpenAI-compatible server, points a throwaway agent home
# at it, and runs a real task through the real CLI. The agent must:
#
#   1. execute a shell command,
#   2. write a file with write_file,
#   3. execute Python,
#   4. save a skill to disk.
#
# Exits non-zero if any of those did not actually happen on disk.
#
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO_DIR/.venv/bin/python"
ACKSTREET="$REPO_DIR/.venv/bin/ackstreet"
E2E_HOME="${E2E_HOME:-/tmp/ackstreet-e2e}"
PORT="${MOCK_PORT:-8099}"

if [ ! -x "$VENV_PY" ]; then
  echo "error: no virtualenv at $REPO_DIR/.venv — run 'make install' first" >&2
  exit 1
fi

echo "==> Cleaning previous run"
rm -rf "$E2E_HOME"
mkdir -p "$E2E_HOME"

echo "==> Starting mock OpenAI-compatible server on port $PORT"
"$VENV_PY" "$REPO_DIR/scripts/mock_openai_server.py" --port "$PORT" >"$E2E_HOME/mock.log" 2>&1 &
MOCK_PID=$!
cleanup() { kill "$MOCK_PID" 2>/dev/null; wait "$MOCK_PID" 2>/dev/null; }
trap cleanup EXIT

# Wait for the server to answer.
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done
curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null || { echo "error: mock server did not start" >&2; cat "$E2E_HOME/mock.log"; exit 1; }
echo "    mock server is up"

echo "==> Initialising a fresh agent home at $E2E_HOME"
export ACKSTREET_HOME="$E2E_HOME"
export MOCK_API_KEY="test-key-not-real"

"$ACKSTREET" init >/dev/null
"$ACKSTREET" config set providers.openai.base_url "http://127.0.0.1:$PORT/v1" >/dev/null
"$ACKSTREET" config set providers.openai.api_key_env "MOCK_API_KEY" >/dev/null
"$ACKSTREET" config set providers.openai.model "mock-model" >/dev/null
"$ACKSTREET" config set agent.auto_curate "false" >/dev/null
echo "    configured to use the mock endpoint"

echo
echo "==> Running a real task through the CLI"
echo "---------------------------------------------------------------"
"$ACKSTREET" run "Prove the loop works: run a shell command, write a file, run python, and save a skill." --verbose
RUN_STATUS=$?
echo "---------------------------------------------------------------"
echo

FAILURES=0
check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS  $label"
  else
    echo "  FAIL  $label"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "==> Verifying artifacts on disk"
check "CLI exited cleanly"                      test "$RUN_STATUS" -eq 0
check "workspace file e2e-proof.txt written"    test -f "$E2E_HOME/workspace/e2e-proof.txt"
check "skill e2e-prove-tool-loop.md saved"      test -f "$E2E_HOME/skills/e2e-prove-tool-loop.md"
check "session recorded in memory"              test -f "$E2E_HOME/memory/index.json"

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "==> ALL CHECKS PASSED"
  echo
  echo "File written by the agent:"
  sed 's/^/    /' "$E2E_HOME/workspace/e2e-proof.txt"
  echo
  echo "Skill saved by the agent:"
  sed -n '1,12p' "$E2E_HOME/skills/e2e-prove-tool-loop.md" | sed 's/^/    /'
  exit 0
fi

echo "==> $FAILURES CHECK(S) FAILED"
echo "mock server log:"
cat "$E2E_HOME/mock.log" 2>/dev/null | tail -20
exit 1
