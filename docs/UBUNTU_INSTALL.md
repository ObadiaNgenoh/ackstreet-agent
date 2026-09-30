# Installation Guide for ACKSTREET AGENT on Ubuntu

This guide walks you through a complete one-command installation of ACKSTREET AGENT on Ubuntu (18.04+, 20.04, 22.04, 24.04).

## Prerequisites

- Ubuntu 18.04 LTS or newer
- Internet connection
- `curl` or `wget` installed (usually pre-installed)
- About 500MB disk space
- ~1GB RAM minimum

## Quick Start (One Command)

```bash
curl -fsSL https://raw.githubusercontent.com/ObadiaNgenoh/ackstreet-agent/main/install.sh | bash
```

That's it! The installer will:

1. ✓ Check for Python 3.9+
2. ✓ Clone the repository
3. ✓ Create a Python virtual environment
4. ✓ Install the agent and dependencies
5. ✓ Launch an interactive setup wizard
6. ✓ Guide you through LLM provider configuration
7. ✓ Optionally configure Telegram/WhatsApp
8. ✓ Create the config at `~/.ackstreet/config.toml`
9. ✓ Store API keys securely in `~/.ackstreet/.env`

## Step-by-Step Installation

### Step 1: Prepare Your System

```bash
# Update package lists
sudo apt-get update

# Install required dependencies
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  curl
```

### Step 2: Run the Installer

```bash
# Option A: From the web (recommended)
curl -fsSL https://raw.githubusercontent.com/ObadiaNgenoh/ackstreet-agent/main/install.sh | bash

# Option B: From a local clone
git clone https://github.com/ObadiaNgenoh/ackstreet-agent.git
cd ackstreet-agent
./install.sh
```

### Step 3: Follow the Interactive Setup Wizard

The installer will ask you:

**1. Messaging Gateway (optional)**
```
Do you want to set up a messaging gateway (Telegram/WhatsApp)? [y/N]
```
- Choose `y` for Telegram or WhatsApp
- Choose `n` to skip (you can set it up later)

**2. LLM Provider**

Choose one:
- **OpenAI** - GPT-4o, GPT-4o-mini ($0.15-15/1M tokens)
- **Anthropic** - Claude 3.5 Sonnet ($3-15/1M tokens)
- **Ollama** - Local LLaMA (free, requires 8GB+ RAM)
- **Custom** - LM Studio, vLLM, Azure OpenAI

**3. Security Settings**

Allow these tools?
- Shell execution (recommended: yes)
- Web search (recommended: yes)
- Python execution (recommended: yes)

### Step 4: Add to PATH (Optional but Recommended)

After installation, add the agent to your PATH:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Verify installation:
```bash
ackstreet --version
```

## Configuration

### Your Config Files

After installation, you have:

```
~/.ackstreet/
├── config.toml          # Main configuration (model, tools, approval mode)
├── .env                 # API keys (permissions: 0600, private)
├── skills/              # Learned skills (auto-created)
├── memory/              # Session memory and facts
├── workspace/           # Working directory for the agent
└── logs/                # Execution logs
```

### Configure Your LLM Provider

#### Option A: OpenAI

1. Get your API key from [platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys)

2. Store it securely:
```bash
echo 'OPENAI_API_KEY=sk-proj-your-key-here' >> ~/.ackstreet/.env
```

3. Verify:
```bash
ackstreet doctor
```

#### Option B: Anthropic (Claude)

1. Get your API key from [console.anthropic.com/account/keys](https://console.anthropic.com/account/keys)

2. Store it:
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-your-key-here' >> ~/.ackstreet/.env
```

3. Update config:
```bash
ackstreet config set agent.provider anthropic
ackstreet config set agent.model claude-3-5-sonnet-20241022
```

#### Option C: Ollama (Local, Free)

1. Install Ollama from [ollama.com](https://ollama.com)

2. Start the service:
```bash
ollama serve &
```

3. Pull a model:
```bash
ollama pull llama3.1
```

4. Configure:
```bash
ackstreet config set agent.provider ollama
ackstreet config set agent.model llama3.1
```

### Verify Installation

```bash
ackstreet doctor
```

This checks:
- Configuration file exists ✓
- LLM provider is reachable ✓
- API keys are set ✓
- Tools are available ✓
- Chat connectors (if configured)

## First Run

### Interactive Chat

```bash
ackstreet chat
```

Commands in chat:
- `/help` - Show help
- `/tools` - List available tools
- `/skills` - Show learned skills
- `/memory` - Show memory stats
- `/exit` - Quit

Example:
```
you > What's in the current directory?
  [thinking...]
  -> shell ls -la
     ok Listed 15 items
Answer: ...
```

### Single Task (Non-interactive)

```bash
ackstreet run "List all Python files and count them"
```

### Health Check

```bash
ackstreet doctor
```

## Messaging Gateway Setup

### Telegram

After installation, connect Telegram:

```bash
ackstreet connect telegram --token <YOUR_BOT_TOKEN>
ackstreet connect telegram --allow-user <YOUR_USER_ID>
ackstreet serve telegram
```

**Get a Telegram Bot Token:**
1. Open Telegram
2. Start a chat with `@BotFather`
3. Send `/newbot`
4. Follow the prompts (choose a name, then username)
5. Copy the token (format: `123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd`)

**Get Your User ID:**
1. Send `/whoami` to your bot
2. It replies with your numeric User ID

**Run the bot:**
```bash
ackstreet serve telegram
```

The agent will now respond to messages in Telegram.

### WhatsApp

```bash
ackstreet connect whatsapp
ackstreet serve whatsapp
```

⚠️ **Warning**: WhatsApp uses an unofficial protocol and Meta may ban accounts. Use at your own risk.

## Persistent Service (Systemd)

To run the agent continuously:

```bash
# Create a systemd service file
sudo tee /etc/systemd/system/ackstreet-telegram.service > /dev/null << 'EOF'
[Unit]
Description=ACKSTREET AGENT Telegram Connector
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$HOME/.ackstreet
Environment="PATH=$HOME/.local/bin:$PATH"
ExecStart=/home/$USER/.ackstreet/src/.venv/bin/ackstreet serve telegram
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

Replace `$USER` with your actual username.

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable ackstreet-telegram
sudo systemctl start ackstreet-telegram

# Check status
sudo systemctl status ackstreet-telegram

# View logs
sudo journalctl -u ackstreet-telegram -f
```

## Troubleshooting

### Python Not Found

```bash
sudo apt-get install -y python3 python3-venv python3-pip
```

### Permission Denied on install.sh

```bash
chmod +x install.sh
./install.sh
```

### "ackstreet command not found"

Add to PATH:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Provider Not Responding

Check credentials:
```bash
ackstreet doctor
```

Make sure:
- API key is in `~/.ackstreet/.env`
- No typos in the key
- Account has remaining credits

### Agent Hangs on Tool Execution

Check timeout settings:
```bash
ackstreet config show
```

Adjust if needed:
```bash
ackstreet config set tools.shell_timeout 120
ackstreet config set tools.web_timeout 60
```

### Telegram Token Invalid

Verify with:
```bash
ackstreet connect telegram --token YOUR_TOKEN --no-verify false
```

Or test manually:
```bash
curl https://api.telegram.org/botYOUR_TOKEN/getMe
```

## Uninstall

```bash
# Remove the agent
rm -rf ~/.ackstreet ~/ackstreet-agent

# Remove PATH entry
# Edit ~/.bashrc and remove the ACKSTREET line
nano ~/.bashrc
```

## Next Steps

1. **Chat**: `ackstreet chat`
2. **Run tasks**: `ackstreet run "your task"`
3. **Learn skills**: The agent auto-saves reusable procedures
4. **Connect Telegram**: `ackstreet connect telegram`
5. **Check memory**: `ackstreet memory stats`

## Get Help

- **Documentation**: [github.com/ObadiaNgenoh/ackstreet-agent](https://github.com/ObadiaNgenoh/ackstreet-agent)
- **Issues**: [github.com/ObadiaNgenoh/ackstreet-agent/issues](https://github.com/ObadiaNgenoh/ackstreet-agent/issues)
- **Config reference**: `ackstreet config show`

## Security Notes

- **API Keys**: Stored in `~/.ackstreet/.env` with permissions `0600` (read-only by you)
- **Shell Access**: By default, the agent can execute shell commands. Set `tools.allow_shell = false` if untrusted input is used.
- **Telegram Allowlist**: Always set `allowed_user_ids` to restrict who can control the bot.
- **Memory**: Sessions and facts are stored locally in `~/.ackstreet/memory/`.

---

**That's it!** You now have ACKSTREET AGENT running on your Ubuntu server. Start with `ackstreet chat` and explore.
