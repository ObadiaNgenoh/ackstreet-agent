# Running ACKSTREET AGENT on a fresh VM

A complete walkthrough from a bare virtual machine to a working agent, for
Debian/Ubuntu, Fedora/RHEL, and Windows (WSL2). Budget about ten minutes on a
small instance (1 vCPU / 1 GB RAM is enough for the agent itself).

> **Resource note.** The agent is lightweight — it is a Python process that makes
> HTTP calls. Memory and CPU matter only if you run a **local model**, which is a
> separate concern covered in step 6.

---

## 1. Create the VM

Any small instance works. If you have nothing running yet:

| Provider | Suggested shape | Notes |
|---|---|---|
| AWS EC2 | `t3.small`, Ubuntu 24.04 | 2 GB RAM leaves headroom |
| GCP | `e2-small`, Debian 12 | |
| Azure | `Standard_B1s`, Ubuntu 24.04 | |
| Hetzner / DigitalOcean | cheapest 1–2 GB droplet | Best value for this workload |

Open **no inbound ports**. The agent makes outbound HTTPS calls only; it needs
no listener.

---

## 2. Connect and update

```bash
ssh your-user@YOUR_VM_IP
sudo apt-get update && sudo apt-get upgrade -y
```

---

## 3. Install Python 3.9+ and git

**Debian / Ubuntu:**

```bash
sudo apt-get install -y python3 python3-venv python3-pip git curl
python3 --version          # expect 3.9 or newer
```

**Fedora / RHEL / Rocky:**

```bash
sudo dnf install -y python3 python3-pip git curl
python3 --version
```

**Amazon Linux 2023:**

```bash
sudo dnf install -y python3 python3-pip git curl
```

> If `python3-venv` is missing, `python3 -m venv` fails with
> *"ensurepip is not available"*. Install the venv package — on Debian/Ubuntu it
> is a separate package from `python3` itself.

---

## 4. Install ACKSTREET AGENT

**Option A — one command (recommended):**

```bash
curl -fsSL https://raw.githubusercontent.com/ObadiaNgenoh/ackstreet-agent/main/install.sh | bash
```

**Option B — clone and run the installer:**

```bash
git clone https://github.com/ObadiaNgenoh/ackstreet-agent.git
cd ackstreet-agent
./install.sh
```

The installer will:

1. locate a suitable Python,
2. clone/pull the repo into `~/.ackstreet/src`,
3. create a virtualenv at `~/.ackstreet/src/.venv`,
4. install the package,
5. write `~/.ackstreet/config.toml` and seed the starter skills,
6. symlink `ackstreet` into `~/.local/bin`,
7. run a health check.

If `~/.local/bin` is not on your `PATH`:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

## 5. Give it a model

### 5a. Hosted provider

```bash
export OPENAI_API_KEY=sk-...              # or ANTHROPIC_API_KEY=sk-ant-...
ackstreet config set agent.provider openai  # or: anthropic
ackstreet doctor
```

### 5b. Make the key persistent

Environment variables set in an SSH session disappear on logout. Put them in a
file only your user can read:

```bash
cat > ~/.ackstreet/.env <<'EOF'
export OPENAI_API_KEY=sk-...
EOF
chmod 600 ~/.ackstreet/.env
echo 'source ~/.ackstreet/.env' >> ~/.bashrc
source ~/.bashrc
```

### 5c. Verify

```bash
ackstreet doctor
```

You want every line in section 4 ("Backends") to read `[ok]`. Section 3
("Credentials") must show your key as present.

---

## 6. Optional — fully local models with Ollama

Nothing leaves the machine, and there is no per-token cost. This needs real RAM:
**8 GB minimum for a 7B model**, 16 GB+ for anything larger. On a 1 GB VM, skip
this and use a hosted provider.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1          # ~4.7 GB download
ollama serve &                # listens on 127.0.0.1:11434

ackstreet config set agent.provider ollama
ackstreet doctor
```

> **Small VM swap tip.** If you insist on running a local model on a 1–2 GB
> instance, add swap first or the model load will be OOM-killed:
> ```bash
> sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
> sudo mkswap /swapfile && sudo swapon /swapfile
> echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
> ```
> It will be slow, but it will not be killed.

If Ollama runs on a **different** host than the agent:

```bash
ackstreet config set providers.ollama.base_url http://MODEL_HOST:11434
```

---

## 7. First real task

```bash
ackstreet run "list the python files in this directory and write a summary to summary.txt"
```

Then confirm it learned something:

```bash
ackstreet skills list
ackstreet memory sessions
```

---

## 8. Make it survive reboots (optional)

### systemd service

Useful if you drive the agent from a script or want a persistent chat session.

```bash
sudo tee /etc/systemd/system/ackstreet.service >/dev/null <<EOF
[Unit]
Description=ACKSTREET AGENT (idle keep-alive)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
EnvironmentFile=$HOME/.ackstreet/.env
Environment=ACKSTREET_HOME=$HOME/.ackstreet
WorkingDirectory=$HOME
ExecStart=/bin/sh -c 'while true; do sleep 3600; done'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ackstreet
```

This keeps the environment warm so scheduled invocations (`ackstreet run ...`
from cron) always have the key and config available.

### Cron example

```bash
crontab -e
# Every weekday at 08:00, write a status note.
0 8 * * 1-5 ACKSTREET_HOME=$HOME/.ackstreet $HOME/.ackstreet/src/.venv/bin/ackstreet run "summarise yesterday's changes" >> $HOME/ackstreet-cron.log 2>&1
```

---

## 9. Docker on the VM

If you would rather not install Python on the host:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/ObadiaNgenoh/ackstreet-agent.git
cd ackstreet-agent
cp .env.example .env      # fill in your key
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml exec ackstreet ackstreet chat
```

State lives in the `ackstreet-home` named volume, so skills and memory survive
rebuilds. To back it up:

```bash
docker run --rm -v ackstreet-home:/data -v "$PWD":/backup alpine \
  tar czf /backup/ackstreet-backup.tar.gz -C /data .
```

---

## 10. Windows via WSL2

```powershell
wsl --install -d Ubuntu
```

Then inside the Ubuntu shell, follow steps 3–7 above exactly as on Linux. Notes:

- Keep the install **inside** the Linux filesystem (`~/...`), not on `/mnt/c/...`.
  Cross-filesystem I/O is dramatically slower and file permissions behave oddly.
- If the shell reports a virtualisation error, enable the *Virtual Machine
  Platform* and *Windows Subsystem for Linux* features in Windows Features and
  reboot.

---

## Security checklist

The agent executes shell commands **by design** — that is the point of it. Treat
it with the same care as giving someone an SSH login.

- [ ] **Do not run it as root.** Use your normal user account.
- [ ] **Keep the API key file at mode 600** (`chmod 600 ~/.ackstreet/.env`).
- [ ] **Restrict who can SSH in**; prefer key auth and disable password login.
- [ ] **Review the command blocklist** in `~/.ackstreet/config.toml`
      (`tools.blocked_commands`) and add anything dangerous in your environment.
- [ ] **Turn off what you do not need:** `tools.allow_web`, `tools.allow_python`,
      or `tools.allow_shell` can each be set to `false`.
- [ ] **Read the skills it writes.** They live in `~/.ackstreet/skills/` as plain
      markdown. The agent authors them itself, so review before trusting a
      procedure blindly in production.
- [ ] **Container isolation** (section 9) gives you a much tighter blast radius
      than a bare host install if you are running untrusted tasks.
- [ ] **Audit the transcripts** in `~/.ackstreet/memory/sessions/` — they record
      every command the agent ran.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ensurepip is not available` | venv module missing | `sudo apt-get install -y python3-venv` |
| `command not found: ackstreet` | `~/.local/bin` not on PATH | add it to `~/.bashrc` and re-source |
| `HTTP 401` in `doctor` | key unset or invalid | `source ~/.ackstreet/.env`, then re-run |
| `HTTP 404` in `doctor` | wrong `base_url` or model name | `ackstreet config show` to inspect |
| `cannot reach http://localhost:11434` | Ollama not running | `ollama serve &` |
| `model 'x' was not found` | model not pulled | `ollama pull x` |
| Agent silently does nothing | no provider key resolved | `ackstreet doctor`, check section 3 |
| Config file corrupt after editing by hand | invalid TOML | `ackstreet config show` fails loudly; restore from `~/.ackstreet/config.toml` backup |

Every diagnostic question starts here:

```bash
ackstreet doctor
```
