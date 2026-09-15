# ACKSTREET AGENT

A self-hosted, self-improving AI agent framework.

ACKSTREET AGENT runs on your own machine, talks to the LLM provider of your choice,
uses real tools (shell, files, web, Python), remembers what happened across sessions,
and **writes its own reusable skills** after it finishes tasks — so it gets better at
your recurring work the more you use it.

```
   _   ___ _  __ ___ _____ ___ ___ _____   _   ___ ___ _  _ _____
  /_\ / __| |/ // __|_   _| _ \ __|_   _| /_\ / __| __| \| |_   _|
 / _ \ (__| ' < \__ \ | | |   / _|  | |  / _ \ (_ | _|| .` | | |
/_/ \_\___|_|\_\|___/ |_| |_|_\___| |_| /_/ \_\___|___|_|\_| |_|
```

---

## What it does

| Capability | Detail |
|---|---|
| **Conversational agent** | Interactive `chat` mode plus one-shot `run` for scripted tasks |
| **Tool use** | Shell execution, file read/write/edit/search, web search, page fetching, Python execution |
| **Multi-step loops** | Plan → act → observe → repeat, with a step budget and repeat-call detection |
| **Self-improvement** | After a substantive session it writes a new skill (or refines an existing one) to disk |
| **Skill reuse** | A compact skills index goes into the system prompt; full procedures load on demand |
| **Persistent memory** | Session transcripts, an index for cross-session recall, and durable facts |
| **Multi-provider** | OpenAI-compatible endpoints, Anthropic, and local models via Ollama |
| **Chat connectors** | Talk to the agent from **Telegram** (bot token, long polling) or **WhatsApp** (QR-linked multi-device), with per-chat sessions, a user allowlist, and approvals answered in the chat |
| **Configurable** | Plain TOML config file plus `ACKSTREET_*` environment overrides |

---

## Install

### One-command installer (Linux, macOS, WSL)

```bash
curl -fsSL https://raw.githubusercontent.com/ObadiaNgenoh/ackstreet-agent/main/install.sh | bash
```

Or from a clone:

```bash
git clone https://github.com/ObadiaNgenoh/ackstreet-agent.git
cd ackstreet-agent
./install.sh
```

The installer finds a Python ≥3.9, creates a virtualenv in `./.venv`, installs the
package, writes `~/.ackstreet/config.toml`, seeds the starter skills, and runs
`ackstreet doctor`.

### Manual install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
ackstreet init
ackstreet doctor
```

Requires **Python 3.9+**. The only hard dependency is `httpx`.

### Docker

```bash
docker compose -f docker/docker-compose.yml up -d ackstreet
docker compose -f docker/docker-compose.yml exec ackstreet ackstreet chat
```

See [`docs/VM_SETUP.md`](docs/VM_SETUP.md) for a full fresh-VM walkthrough.

---

## Configure a provider

Pick one of the three supported backends.

**OpenAI-compatible** (OpenAI, Azure, OpenRouter, vLLM, LM Studio, llama.cpp, Groq, DeepSeek…):

```bash
export OPENAI_API_KEY=sk-...
ackstreet config set agent.provider openai
ackstreet config set providers.openai.model gpt-4o-mini
```

**Anthropic:**

```bash
export ANTHROPIC_API_KEY=sk-ant-...
ackstreet config set agent.provider anthropic
ackstreet config set providers.anthropic.model claude-sonnet-4-5
```

**Local models via Ollama** (no API key, nothing leaves the machine):

```bash
ollama serve
ollama pull llama3.1
ackstreet config set agent.provider ollama
```

Point at any other OpenAI-compatible server by editing `~/.ackstreet/config.toml`:

```toml
[providers.custom]
type = "openai"
base_url = "http://localhost:8000/v1"
api_key_env = "CUSTOM_API_KEY"
model = "my-local-model"
```

Then `ackstreet config set agent.provider custom`.

---

## Usage

```bash
ackstreet init                      # create config + directory layout
ackstreet doctor                    # verify config, keys, provider reachability, tools
ackstreet chat                      # interactive session
ackstreet chat --no-stream          # if your terminal mangles streaming output
ackstreet run "summarise every CSV in ./data into one report"
ackstreet run "..." --plan          # produce a plan before acting
ackstreet run "..." --verbose       # show every tool call
ackstreet run "..." --json          # machine-readable result
ackstreet run "..." --yes           # approve dangerous tools without prompting
ackstreet run "..." --no-approval   # alias for --yes
ackstreet --approval-mode ask run "..."   # per-run override of the gate
ackstreet plan "migrate this repo to pyproject"
ackstreet tools                     # list tools and their arguments
ackstreet skills list               # inspect what the agent has learned
ackstreet skills show <name>        # read one skill in full
ackstreet skills edit <name>        # open it in $EDITOR
ackstreet memory sessions           # recent runs
ackstreet memory recall "kubernetes"
```

Inside `chat`: `/help`, `/tools`, `/skills`, `/approvals`, `/memory`, `/clear`, `/exit`.

When the approval gate is active, a dangerous tool call pauses and asks:

```
  ! approval required (mode=ask)
    shell  rm -rf build && make dist
    approve? [y]es / [n]o / [a]lways allow 'shell' for this session >
```

`a` remembers the tool for the rest of the session.

### Example

```bash
$ ackstreet run "count the Python files in this repo and write the count to stats.txt" --verbose
  -> shell        {"command": "find . -name '*.py' -not -path './.venv/*' | wc -l"}
     ok  exit_code: 0 ...
  -> write_file   {"path": "stats.txt", "content": "Python files: 14\n"}
     ok  created /home/you/.ackstreet/workspace/stats.txt
  * Learned new skill: count-files-in-repo — ...
```

---

## Skills and self-improvement

A skill is a markdown file with YAML frontmatter, stored at `~/.ackstreet/skills/<slug>.md`:

```markdown
---
name: deploy-static-site
description: Publish a static directory to the internal host and verify it serves. Use when deploying HTML/CSS/JS.
version: 1.0.0
author: ackstreet-agent
tags: [deploy, ops]
source: curator
---

# Skill: Deploy Static Site

## When to Use
...

## Steps
1. ...

## Pitfalls
- ...

## Verification
- ...
```

Two ways skills are created:

1. **Automatically** — after a session with at least `skills.min_steps_to_curate` tool
   calls, the curator is asked whether the session contained a reusable procedure. If
   yes, it writes one. If a skill of that name already exists, the curator *updates*
   it instead of creating a near-duplicate.
2. **Explicitly** — the agent can call `save_skill` mid-task, or you can run
   `ackstreet skills create`.

Every skill is plain text you can read, diff, hand-edit, or delete. Set
`agent.auto_curate = false` to turn automatic learning off.

Only names and descriptions go into the system prompt; full bodies load on demand
through the `load_skill` tool, so hundreds of skills stay cheap.

---

## Talk to it from Telegram or WhatsApp

A *connector* lets you message the agent from a chat app. Incoming messages go
through **the same agent loop and the same approval gate** as the CLI — a connector
only moves text in and out, so it cannot bypass the safety checks.

Each chat gets its own conversation. Two people messaging the same bot never see
each other's history, and a stranger who finds the bot cannot drive your machine
if you set the allowlist.

> ### ⚠️ Security warning
>
> **Anyone who can message the bot can use the agent on this machine** — including
> shell commands and file writes — unless you restrict the allowlist.
>
> The allowlist is **empty by default**, which allows everyone. Set it before you
> expose the bot:
>
> ```bash
> ackstreet connect telegram --allow-user 123456789
> ```
>
> Use `/whoami` in the chat to learn your own id, or set `'*'` to allow everyone
> deliberately. `ackstreet doctor` and `ackstreet connectors` both warn while the
> list is empty.

### Telegram (works behind NAT — no public URL)

The Bot API supports **long polling**, so the bot needs no inbound port, no public
URL and no TLS certificate. It runs fine on a home VM behind a router.

1. Open Telegram and start a chat with [**@BotFather**](https://t.me/BotFather).
2. Send `/newbot`, then follow the prompts: choose a display name, then a username
   that ends in `bot` (e.g. `my_ackstreet_bot`).
3. BotFather replies with a token like `123456789:AAE...xyz`.
4. Store and verify it:

   ```bash
   ackstreet connect telegram --token 123456789:AAE...xyz
   ```

   This calls `getMe` and prints the bot's `@username` so you know the token works.
5. Optionally lock it to your account (see the warning above):

   ```bash
   ackstreet connect telegram --allow-user 123456789
   ```
6. Start listening:

   ```bash
   ackstreet serve telegram
   ```
7. Message your bot in Telegram and it answers.

`Ctrl-C` stops it. The token can also come from `ACKSTREET_TELEGRAM_BOT_TOKEN` or
`TELEGRAM_BOT_TOKEN` instead of the config file, which is handy on a VM.

### WhatsApp (QR-linked, multi-device)

WhatsApp has no bot API for personal accounts, so this uses the **WhatsApp Web
multi-device protocol** — the same mechanism as WhatsApp Web and Desktop — through
the [neonize](https://github.com/krypton-byte/neonize) binding to `whatsmeow`.

> **Unofficial protocol: use at your own risk.** Automating a personal account is
> outside WhatsApp's terms and can get the number banned. Prefer Telegram for
> anything you depend on.

1. Install the optional extras:

   ```bash
   pip install 'ackstreet-agent[whatsapp]'
   ```
2. Run the connect command and scan:

   ```bash
   ackstreet connect whatsapp
   ```
3. The terminal draws a QR code. On your phone open **WhatsApp → Settings →
   Linked devices → Link a device** and scan it.
4. Once linked, the login is saved to `~/.ackstreet/whatsapp/session.db` and
   **survives restarts** — no re-scanning. Override the path with
   `--session-path` or `ACKSTREET_WHATSAPP_SESSION`.
5. Lock it down, then run it:

   ```bash
   ackstreet connect whatsapp --allow-user 254700000001
   ackstreet serve whatsapp
   ```

### How approvals work in a chat

If `agent.approval_mode` is `ask` (or `allowlist` with a non-matching call), the
agent **asks you in the chat** instead of at a terminal prompt:

```
Approval needed: the agent wants to run shell.
  npm run deploy
Reply 'yes' to allow once, 'always' to allow shell for this chat, or 'no' to refuse.
```

- `yes` → the call runs once, `always` → that tool is trusted for that chat,
  `no` → it is refused and the agent is told so.
- **A timeout is a refusal.** So is failing to deliver the question — never the
  other way round. Tune with `connectors.approval_timeout` (default 300s).
- The denylist and the shell blocklist still apply on top; a denied call is refused
  even if you type `yes`.
- Running completely unattended? `ackstreet serve telegram --yes` locks the gate
  open for that session. Don't do that on a bot anyone can reach.

### Built-in chat commands

Any message starting with `/` is answered locally, without a model call:

| Command | What it does |
|---|---|
| `/help` | List the commands |
| `/status` | Provider, approval mode, tool/skill counts, session state |
| `/skills` | Skills the agent has learned |
| `/tools` | Registered tools |
| `/clear` | Forget this chat's context (learned skills are kept) |
| `/whoami` | Show the chat id and your user id — use this to fill the allowlist |

Anything else is treated as a task and runs through the normal agent loop.

### Configuration

```toml
[connectors]
enabled = true
allowed_user_ids = []      # shared across platforms; empty = everyone
allow_group_chats = false  # group chats are ignored unless enabled
session_ttl = 3600         # seconds of idle before a chat's context is dropped
approval_timeout = 300     # seconds to wait for a yes/no before refusing

[connectors.telegram]
bot_token = ""
allowed_user_ids = []      # merged with the shared list above

[connectors.whatsapp]
session_path = ""          # empty = <ACKSTREET home>/whatsapp/session.db
allowed_user_ids = []
```

### Adding a third platform

Write one `Connector` subclass and decorate it with `@register`. The agent core is
never touched — the registry, CLI and `doctor` discover it automatically:

```python
from ackstreet.connectors import Connector, IncomingMessage, register

@register
class MyConnector(Connector):
    name = "mine"
    display_name = "My Platform"
    extra = "mine"          # the pip extra that installs its dependencies

    def listen(self, on_message=None, stop_event=None): ...
    def send(self, chat_id, text, reply_to=None): ...
```

Everything else — the allowlist, per-chat sessions, routing, approval-over-chat,
and command handling — is inherited from the shared core. Connector dependencies
are extras, so the base install stays lightweight:

```bash
pip install -e .                      # core only
pip install -e '.[telegram]'          # Telegram
pip install -e '.[whatsapp]'          # WhatsApp (neonize + qrcode)
pip install -e '.[all]'               # everything
```

---

## Architecture

```
ackstreet/
├── cli.py             argparse CLI: init, doctor, chat, run, plan, skills, memory, config, tools
├── agent.py           the plan→act→observe loop, context trimming, guards, curation trigger
├── config.py          TOML + ACKSTREET_* env resolution, provider lookup, paths
├── memory.py          session transcripts, recall index, durable facts
├── errors.py          exception taxonomy
├── providers/
│   ├── base.py        Message / ToolCall / ProviderResponse, shared HTTP + SSL handling
│   ├── openai_compat.py   /chat/completions for any compatible server (streaming)
│   ├── anthropic_provider.py  /v1/messages, incl. block-format translation
│   ├── ollama_provider.py     local /api/chat
│   └── __init__.py    registry + factory
├── tools/
│   ├── base.py        Tool / ToolResult, validation, path resolution
│   ├── shell.py       subprocess execution with blocklist + timeout
│   ├── files.py       read / write / edit / list / search / delete
│   ├── web.py         search (Tavily → Serper → Brave → DuckDuckGo) + fetch + HTML→text
│   ├── python_exec.py isolated child interpreter
│   └── __init__.py    ToolRegistry + build_default_registry
├── skills/
│   ├── registry.py    frontmatter parse/dump, CRUD, index rendering, search
│   ├── curator.py     reflection pass that proposes and commits skills
│   └── tools.py       list_skills / load_skill / save_skill / update_skill / search_skills
├── connectors/
│   ├── base.py        Connector ABC, message types, the user allowlist, chunking
│   ├── sessions.py    one Agent + history per chat, TTL eviction
│   ├── router.py      message -> agent loop, replies back, approval-over-chat
│   ├── registry.py    pluggable connector registry
│   ├── telegram.py    Bot API long polling (no public URL needed)
│   ├── whatsapp.py    WhatsApp Web multi-device via neonize, terminal QR
│   └── commands.py    `connect` / `serve` / `connectors` CLI implementations
└── seeds/skills/      starter skills copied on first init
```

Design notes:

- **Provider-agnostic core.** Everything above `providers/` thinks in `Message`,
  `ToolCall` and `ProviderResponse`. Adding a backend is one file plus a registry entry.
- **Tools never crash the loop.** Expected failures return `ToolResult(ok=False)` with a
  message the model can read and recover from.
- **Malformed tool arguments are survivable.** Non-JSON model output is caught and fed
  back as a correctable error rather than raising.
- **Python runs out-of-process** so generated code cannot take the agent down.
- **Connectors cannot bypass the gate.** A connector only moves text in and out; the
  router hands accepted text to the same `Agent` the CLI builds, and supplies an
  *approver callback* so a dangerous call asks the human in the chat. A connector
  never calls a tool directly.
- **Incoming messages are handled on worker threads** (`MessageRouter.dispatch_async`),
  not on the listener thread. This is a correctness requirement, not throughput: an
  agent turn waiting on an approval prompt blocks its thread, so a single-threaded
  listener could never read the user's "yes" out of the platform's queue.

Full detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Configuration reference

`~/.ackstreet/config.toml` (created by `ackstreet init`):

```toml
[agent]
name = "Ackstreet"
provider = "openai"          # which [providers.*] entry to use
model = ""                   # empty = inherit the provider's model
max_steps = 25               # hard cap on tool-calling steps per task
temperature = 0.2
context_messages = 40        # recent messages kept in the model window
auto_curate = true           # write skills after substantive sessions
auto_load_skill = true       # inject the skills index into the system prompt

# Approval gate for dangerous tools (shell, write_file, edit_file,
# delete_file, python, save_skill, update_skill).
approval_mode = "auto"       # "auto" | "ask" | "allowlist"
approval_allowlist = []      # e.g. ["ls", "git status*", "read_file:docs/*"]
approval_denylist = []       # refused in EVERY mode, even "auto"

[tools]
allow_shell = true
allow_web = true
allow_python = true
shell_timeout = 60
web_timeout = 30
max_output_chars = 20000
# Second layer: screened inside ShellTool, applies in every approval mode.
blocked_commands = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero of=/dev/", "shutdown", "reboot"]

[memory]
enabled = true
recall_limit = 8

[skills]
enabled = true
min_steps_to_curate = 3
```

Provider tuning lives under `[providers.<name>]`:

```toml
[providers.openai]
type = "openai"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"
max_retries = 2              # retries after the first attempt (429/5xx/timeout)
retry_base_delay = 0.5       # exponential backoff seed, in seconds
retry_max_delay = 8.0        # backoff ceiling
```

Any key can be overridden by an environment variable:

```bash
export ACKSTREET_AGENT_PROVIDER=ollama
export ACKSTREET_AGENT_MAX_STEPS=40
export ACKSTREET_AGENT_APPROVAL_MODE=ask
export ACKSTREET_TOOLS_ALLOW_SHELL=false
```

Other variables: `ACKSTREET_HOME` (state directory), `ACKSTREET_CONFIG` (config path),
`ACKSTREET_CA_BUNDLE` (custom CA), plus provider keys (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `SERPER_API_KEY`, `BRAVE_API_KEY`).

Provider endpoints and models can also be redirected without touching the config file,
which is what containers and CI jobs use:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8099/v1   # proxy, gateway or mock server
export OPENAI_API_KEY=test
```

Also supported: `OPENAI_API_BASE`, `OPENAI_MODEL`, `ANTHROPIC_BASE_URL`,
`ANTHROPIC_MODEL`, `OLLAMA_HOST`, `OLLAMA_MODEL`, `CUSTOM_BASE_URL`, `CUSTOM_MODEL`.
See [`.env.example`](.env.example).

---

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

The suite covers config merging and env overrides, every tool, skill parsing and CRUD,
the curator, provider wire formats **and failure modes**, the approval gate, the
agent loop (tool chaining, step budget, repeat detection, memory persistence, context
trimming), and the **chat connectors** (routing, replies, per-chat isolation, the
allowlist, and approval-over-chat) — using a scripted in-process provider and fake
platform APIs, so it needs **no API key, no bot token and no network**.

Current total: **316 tests**. Run just one area with, for example:

```bash
pytest tests/test_approval.py -q            # the gate
pytest tests/test_provider_hardening.py -q  # retries, timeouts, bad responses
pytest tests/test_connectors.py -q          # routing, isolation, approvals in chat
pytest tests/test_telegram.py -q            # Bot API transport, parsing, polling
pytest tests/test_whatsapp.py -q            # multi-device parsing, QR, session file
```

To exercise the real HTTP path without a key, a mock OpenAI-compatible server ships
with the repo:

```bash
python scripts/mock_openai_server.py --port 8099 &
export ACKSTREET_HOME=/tmp/ackstreet-e2e
export MOCK_API_KEY=test
ackstreet config set providers.openai.base_url http://127.0.0.1:8099/v1
ackstreet config set providers.openai.api_key_env MOCK_API_KEY
ackstreet config set providers.openai.model mock-model
ackstreet run "prove the loop works"
```

---

## Status — what is tested vs. what is not

Honest accounting, last updated with the **chat-platform connectors**.
**Verified by execution — 316 automated tests pass, plus real CLI runs:**

- config load/merge, TOML round-trip, env overrides, provider resolution
- **provider endpoint/model env overrides** (`OPENAI_BASE_URL`, `OLLAMA_HOST`, …) —
  added after a CLI run showed they were being ignored
- **`config set` value parsing** — JSON lists/objects, booleans and numbers round-trip,
  so an allowlist is stored as a list rather than a string of characters
- shell execution, exit codes, timeout, command blocklist
- file read/write/edit/search/list, line ranges, unique-match enforcement
- Python execution in a child interpreter, including error propagation
- skill create/get/update/delete/search, frontmatter parse and dump, seed install
- the curator: floor check, decline path, create path, update-instead-of-duplicate
- provider registry, argument parsing, Anthropic message/tool translation
- **retry with exponential backoff** on 429/5xx/read-timeouts, giving up after the
  budget with an error that says so; connect errors and 401/400 are *not* retried
- **clear credential errors** — a missing key fails before the request, naming the
  environment variable to export
- **request timeouts** on every call, with a short connect budget so a dead host
  fails fast instead of hanging
- **malformed / empty / inconsistent responses** raise `ProviderError` rather than
  crashing: no `choices`, a non-object body, invalid JSON, a body-level `error`
  with HTTP 200, `finish_reason=tool_calls` with no usable call, nameless tool
  calls (skipped, not fatal), and unparseable argument JSON (surfaced as a
  `__parse_error__` marker the model can correct)
- **streaming** — incremental text via the callback, fragmented tool calls
  reassembled across chunks, malformed keep-alives ignored, retry before the first
  byte but not mid-stream, and an end-to-end streaming `chat_turn`
- the agent loop end to end against a scripted provider **and** against a real HTTP
  OpenAI-compatible endpoint (the bundled mock server)
- **the approval gate**, through both the agent loop and the real CLI:
  `ask` refuses a non-interactive run, a granted approval runs the tool, a denied
  approval blocks it without crashing the loop, `--yes`/`--no-approval` bypass the
  prompt, `allowlist` permits matching calls and blocks the rest, and the denylist
  refuses in every mode — even against a human "yes"
- memory: session persistence, recall scoring, facts round-trip
- CLI: `init`, `doctor`, `tools`, `skills list`, `run`, `plan`
- **connector core** — a message routed through the router reaches the real agent
  loop and the reply is sent back; per-chat session isolation (two chats get
  separate agents and separate history; the same chat id on two platforms does not
  collide); the allowlist blocks a non-listed user *before* any agent is built,
  matching on bare numbers for WhatsApp JIDs; group chats ignored unless enabled;
  long replies split to fit the platform limit; and **approval-over-chat** — the
  prompt is delivered as a chat message, an in-chat `yes` releases the gated tool
  and it really runs, `no` blocks it, `always` is remembered for that chat, an
  unanswered prompt times out into a refusal, a write that is never approved never
  touches the disk, a denylisted command is refused despite a `yes`, and a failed
  prompt delivery denies rather than allows
- **the async dispatch path** — a gated turn must not stop the next message from
  being read (the deadlock guard), and `wait_for_idle` drains queued work
- **Telegram** — update parsing (private/group, captions, non-text dropped),
  long-poll offset advance, `sendMessage`/`reply_to`/`editMessageText` payloads,
  chunking, `getMe` username capture, and the error paths: missing token, 401,
  409 (another poller), non-JSON body, timeout, and connection failure
- **WhatsApp** (against an injected fake client, since neonize cannot be installed
  here) — protobuf- and dict-shaped message parsing, extended-text and group
  detection, media-only messages dropped, JID normalisation, session-path
  persistence via both config and env, event wiring through both the decorator and
  `add_event_handler` APIs, QR payload handling, and the clear
  `NotInstalledError` that names the pip extra to install
- **the connector loop end to end** against a local fake Bot API and a local
  OpenAI-compatible model: a Telegram update is parsed, routed, hits the approval
  gate, the question is delivered to the chat, the in-chat `yes` opens the gate,
  the real `write_file` tool runs, the file lands on disk, and the reply is sent
  back

Reproduce the proof runs:

```bash
pytest -q                        # 316 tests, no API key / token / network needed
bash scripts/e2e_demo.sh         # real CLI: shell + file write + skill saved
bash scripts/approval_proof.sh   # real CLI: all five gate behaviours
bash scripts/connector_e2e.sh    # real connector -> agent -> gated tool -> reply
```

**Implemented and reviewed, but not verified by execution here:**

- **live calls to OpenAI / Anthropic / Ollama** — no API key and no daemon in this
  environment. The retry, timeout and error-handling paths are covered against
  mocked `httpx` responses, which exercises the real serialisation and parsing
  code, but the model's own behaviour was never observed.
- **a real Telegram bot.** The connector was driven end to end against a local
  HTTP server that speaks the Bot API, not against `api.telegram.org` with a live
  token. The wire format is the documented one and the calls are identical, but no
  message has travelled through Telegram's servers. **You must try this yourself
  with a real BotFather token.**
- **a real WhatsApp login.** `neonize` needs a compiled `whatsmeow` build and could
  not be installed in this environment, and linking requires a physical phone to
  scan the QR. Everything except the client itself — parsing, routing, sessions,
  the allowlist, approvals, session persistence, event wiring — is covered through
  an injected fake client, but **the QR handshake, the socket, and live message
  delivery have never run**. Treat WhatsApp as the less-proven of the two
  connectors.
- **the CI workflow has not run on GitHub yet.** `.github/workflows/ci.yml` is
  valid YAML and every command in it was run locally (ruff, pytest, both e2e
  scripts), but no GitHub Actions run has completed at the time of writing.
- **`fetch_url` and the live web-search backends** (network-dependent)
- **Docker build** — Dockerfile and Compose are written and reviewed; no Docker
  daemon here to build the image.

**Known limitations / not yet built:**

- single-process, synchronous loop — no parallel tool execution
- recall uses token-overlap scoring, not embeddings (deliberate: zero extra deps)
- the approval gate defaults to `auto`, so dangerous tools run unattended unless
  you opt in to `ask` or an allowlist; `ackstreet doctor` warns about this
- connector sessions live in memory (the transcript still reaches disk); a restart
  starts a fresh chat context, though learned skills and facts persist
- connectors are not multiplexed on one port — run one `serve` per platform
- no web UI; the CLI and the chat connectors are the interfaces
- no plugin entry points for third-party tools yet — tools are registered in
  `tools/__init__.py`
- `ackstreet plan` produces a plan but does not gate execution on it

---

## License

MIT — see [LICENSE](LICENSE).
