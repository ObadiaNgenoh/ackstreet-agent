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
ackstreet plan "migrate this repo to pyproject"
ackstreet tools                     # list tools and their arguments
ackstreet skills list               # inspect what the agent has learned
ackstreet skills show <name>        # read one skill in full
ackstreet skills edit <name>        # open it in $EDITOR
ackstreet memory sessions           # recent runs
ackstreet memory recall "kubernetes"
```

Inside `chat`: `/help`, `/tools`, `/skills`, `/memory`, `/clear`, `/exit`.

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

[tools]
allow_shell = true
allow_web = true
allow_python = true
shell_timeout = 60
web_timeout = 30
max_output_chars = 20000
blocked_commands = ["rm -rf /", "mkfs", ":(){:|:&};:", "dd if=/dev/zero of=/dev/", "shutdown", "reboot"]

[memory]
enabled = true
recall_limit = 8

[skills]
enabled = true
min_steps_to_curate = 3
```

Any key can be overridden by an environment variable:

```bash
export ACKSTREET_AGENT_PROVIDER=ollama
export ACKSTREET_AGENT_MAX_STEPS=40
export ACKSTREET_TOOLS_ALLOW_SHELL=false
```

Other variables: `ACKSTREET_HOME` (state directory), `ACKSTREET_CONFIG` (config path),
`ACKSTREET_CA_BUNDLE` (custom CA), plus provider keys (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `SERPER_API_KEY`, `BRAVE_API_KEY`).
See [`.env.example`](.env.example).

---

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

The suite covers config merging and env overrides, every tool, skill parsing and CRUD,
the curator, provider wire formats, and the agent loop (tool chaining, step budget,
repeat detection, memory persistence, context trimming) using a scripted in-process
provider — so it needs **no API key and no network**.

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

Honest accounting. **Verified by execution in this repository's test suite and a real
CLI run:**

- config load/merge, TOML round-trip, env overrides, provider resolution
- shell execution, exit codes, timeout, command blocklist
- file read/write/edit/search/list, line ranges, unique-match enforcement
- Python execution in a child interpreter, including error propagation
- skill create/get/update/delete/search, frontmatter parse and dump, seed install
- the curator: floor check, decline path, create path, update-instead-of-duplicate
- provider registry, argument parsing, Anthropic message/tool translation
- the agent loop end to end against a scripted provider **and** against a real HTTP
  OpenAI-compatible endpoint (the bundled mock server)
- memory: session persistence, recall scoring, facts round-trip
- CLI: `init`, `doctor`, `tools`, `skills list`, `run`, `plan`

**Implemented but not covered by an automated test** (exercise manually):

- live calls to OpenAI / Anthropic / Ollama (no key or daemon in this environment)
- `fetch_url` and the live web-search backends (network-dependent)
- streaming token output in `chat` (the non-streaming path is what the mock exercises)
- Docker build (no Docker daemon in this environment)

**Known limitations / not yet built:**

- single-process, synchronous loop — no parallel tool execution
- recall uses token-overlap scoring, not embeddings (deliberate: zero extra deps)
- no approval gate before dangerous tools (the blocklist and config switches are the
  current guardrails)
- no web UI; the CLI is the interface
- no plugin entry points for third-party tools yet — tools are registered in
  `tools/__init__.py`

---

## License

MIT — see [LICENSE](LICENSE).
