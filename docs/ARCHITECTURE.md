# ARCHITECTURE

How ACKSTREET AGENT is put together, and why.

---

## The loop

Everything the agent does funnels through one synchronous loop in
`ackstreet/agent.py`:

```
        ┌─────────────────────────────────────────────────┐
        │  system prompt                                  │
        │  = base instructions                            │
        │  + skills index          (names + descriptions) │
        │  + recalled memory       (relevant past work)   │
        │  + environment note                             │
        └───────────────────────┬─────────────────────────┘
                                │
                                v
        ┌─────────────────────────────────────────────────┐
    ┌──>│  provider.chat(messages, tools)                 │
    │   └───────────────────────┬─────────────────────────┘
    │                           │
    │              ┌────────────┴────────────┐
    │              v                         v
    │      no tool calls             tool_calls present
    │              │                         │
    │              │                         v
    │              │            ┌────────────────────────┐
    │              │            │ execute each tool      │
    │              │            │ append result as a     │
    │              │            │ role="tool" message    │
    │              │            └───────────┬────────────┘
    │              │                        │
    │              │              ┌─────────┴─────────┐
    │              │              v                   v
    │              │        step budget hit       repeat detected
    │              │              │                   │
    │              └──────────────┘                   │
    │                     continue                    │
    │                                                 v
    └─────────────────────────────────────────────  stop, report
                                │
                                v
        ┌─────────────────────────────────────────────────┐
        │  save session to memory                         │
        │  reflection pass -> skill created or updated    │
        └─────────────────────────────────────────────────┘
```

### Guard rails

| Guard | Where | Behaviour |
|---|---|---|
| **Step budget** | `agent.max_steps` (default 25) | Loop stops with `step_budget_exhausted` |
| **Repeat detection** | `REPEAT_LIMIT = 3` | An identical `(tool, args)` call three times in a row interrupts the loop with `repeat_detected`, and the model is told to change approach |
| **Context trimming** | `agent.context_messages` (default 40) | Oldest turns dropped, with a notice inserted so the model knows history is missing |
| **Command blocklist** | `tools.blocked_commands` | Shell calls matching a blocked pattern are refused before execution |
| **Malformed arguments** | `providers/base.parse_tool_arguments` | Non-JSON model output never raises; it becomes a correctable tool error |
| **Tool isolation** | `Tool.safe_call` | An unexpected exception inside a tool becomes a failed `ToolResult`, never a crash |

---

## Layers

### 1. Config (`config.py`)

Resolution order, lowest to highest priority:

1. `DEFAULTS` dict in the module
2. `~/.ackstreet/config.toml` (or `$ACKSTREET_CONFIG`)
3. `ACKSTREET_<SECTION>_<KEY>` environment variables

Everything the rest of the code needs — directories, provider spec, tool switches,
prompt settings — is read through one `Config` object. Nothing else touches
`os.environ` for configuration.

Paths are centralised: `skills_dir`, `memory_dir`, `sessions_dir`, `workspace`,
`logs_dir`, all under `ACKSTREET_HOME` (default `~/.ackstreet`).

### 2. Providers (`providers/`)

The boundary the rest of the system is written against:

```python
ProviderResponse(
    text: str,
    tool_calls: list[ToolCall],
    finish_reason: str,
    usage: dict,
)
```

Three implementations:

| File | Speaks | Notes |
|---|---|---|
| `openai_compat.py` | `POST {base_url}/chat/completions` | Most portable; true SSE streaming with incremental tool-call assembly |
| `anthropic_provider.py` | `POST {base_url}/v1/messages` | Translates to Anthropic's block format both ways: system is lifted out of the message list, tool results become `tool_result` blocks inside a user turn, tool schemas become `input_schema` |
| `ollama_provider.py` | `POST {base_url}/api/chat` | Local, no key; health check detects a missing model and tells you to `ollama pull` |

`providers/__init__.py` holds the registry and the `type` → class mapping, plus
aliases so `openrouter`, `vllm`, `lmstudio`, `groq`, `deepseek`, etc. all resolve to
the OpenAI-compatible client.

**Adding a provider** is one file plus a registry entry. Nothing above this layer
changes.

### 3. Tools (`tools/`)

```python
class Tool(ABC):
    name: str
    description: str          # the model reads this to decide when to call it
    parameters: dict          # JSON Schema
    dangerous: bool

    def run(self, **kwargs) -> ToolResult: ...
```

| Tool | Capability | Notable behaviour |
|---|---|---|
| `shell` | Run a shell command | Blocklist screening, timeout, returns exit code + stdout + stderr |
| `read_file` | Read text | Optional line range; reports total lines |
| `write_file` | Create/overwrite | Creates parent directories |
| `edit_file` | Exact-string replace | Requires a unique match; `replace_all` for deliberate bulk edits |
| `list_directory` | Enumerate a tree | Skips `.git`, `node_modules`, `.venv`; caps listing length |
| `search_files` | Regex content search | Returns `file:line: text` |
| `delete_file` | Remove a file or tree | Requires `recursive=true` for non-empty directories |
| `web_search` | Internet search | Backend chain: Tavily → Serper → Brave → DuckDuckGo (keyless) |
| `fetch_url` | Fetch a page | Dependency-free HTML→text conversion |
| `python` | Execute Python | **Runs in a child interpreter** via `subprocess` |
| `list_skills`, `load_skill`, `save_skill`, `update_skill`, `search_skills` | Self-management | Bound to the skill registry |

Two design decisions worth calling out:

- **Expected failure is a return value, not an exception.** `ToolResult(ok=False,
  error=...)` is fed to the model, which can then adapt. Only genuinely unexpected
  exceptions are caught by `safe_call` and converted.
- **Path resolution is workspace-relative by default.** A model-supplied relative path
  lands inside `~/.ackstreet/workspace`; absolute paths are honoured. This keeps an
  agent's scratch work predictable.

### 4. Skills (`skills/`)

```
~/.ackstreet/skills/
├── recon-before-acting.md
├── verify-before-claiming-done.md
└── e2e-prove-tool-loop.md          # written by the agent itself
```

`registry.py` is the storage layer: frontmatter parse/dump, CRUD, search, and
`render_index()` which produces the compact catalogue injected into the system prompt.
It prefers PyYAML when installed and falls back to a built-in flat-key parser, so
skills work on a bare install.

`curator.py` is the self-improvement pass. After a session it builds a transcript and
asks the model whether a reusable procedure is present. Two safeguards:

- a **floor** — sessions below `skills.min_steps_to_curate` tool calls are skipped;
- a **novelty check** — a proposal whose slug already exists is merged into the
  existing skill (`_merge_bodies` appends only genuinely new lines) rather than
  creating a near-duplicate.

Curation failure is never fatal: any exception returns `CurationOutcome(changed=False)`
and the run finishes normally.

### 5. Memory (`memory.py`)

Three plain-JSON stores, all human-inspectable:

| File | Contents |
|---|---|
| `memory/sessions/<id>.json` | Full transcript, steps, summary of one run |
| `memory/index.json` | One-line summaries, newest last, capped at 500 |
| `memory/facts.json` | Durable facts recorded via `remember` |

Recall uses **token-overlap scoring**, not embeddings — deliberate, because it needs
zero extra dependencies and no API call. Facts are weighted slightly above sessions.
Swap in a vector store by replacing `MemoryStore.recall`.

### 6. CLI (`cli.py`)

`argparse` with subcommands; no framework dependency.

| Command | Purpose |
|---|---|
| `init` | Create config, directories, seed skills |
| `doctor` | Six-section health check: filesystem, config, credentials, live backend probes, tools, skills/memory |
| `chat` | Interactive loop with `/tools`, `/skills`, `/memory`, `/clear` |
| `run` | One-shot task, with `--plan`, `--verbose`, `--json`, `--quiet` |
| `plan` | Print the plan without acting |
| `tools` | List tools and their arguments |
| `skills` | `list`, `show`, `create`, `edit`, `delete`, `search`, `curate` |
| `memory` | `stats`, `sessions`, `show`, `recall`, `remember`, `facts`, `forget` |
| `config` | `show`, `path`, `edit`, `set` |

Progress is rendered through an event callback (`on_event`), keeping the agent loop
free of any terminal assumptions — the same hook would drive a web UI.

---

## Testing strategy

The suite needs **no API key and no network**.

- **`tests/conftest.py`** provides `ScriptedProvider`, an in-process
  `BaseProvider` that replays a fixed list of `ProviderResponse` objects. This drives
  the real agent loop deterministically: tool chaining, budget exhaustion, repeat
  detection, memory persistence and context trimming are all tested against the actual
  loop code, not a mock of it.
- **`scripts/mock_openai_server.py`** is a real HTTP server speaking the OpenAI
  chat-completions schema. It exercises the genuine network path — serialisation,
  HTTP, response parsing — without a key. Used for the end-to-end proof.

---

## Extension points

| To add… | Do this |
|---|---|
| A tool | Subclass `Tool` in `tools/`, register it in `build_default_registry` |
| A provider | Subclass `BaseProvider` in `providers/`, add to `REGISTRY` |
| A different recall strategy | Replace `MemoryStore.recall` |
| A UI | Pass your own `on_event` callback to `Agent` |
| A scheduler / daemon | Drive `Agent.run` from your own entry point; the CLI is just one caller |
