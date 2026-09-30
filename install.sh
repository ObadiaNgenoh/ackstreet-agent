#!/usr/bin/env bash
#
# ACKSTREET AGENT — one-command installer with interactive onboarding.
#
#   curl -fsSL https://raw.githubusercontent.com/ObadiaNgenoh/ackstreet-agent/main/install.sh | bash
#
# or from a clone:
#
#   ./install.sh
#
# Creates a virtualenv, installs the package, and launches interactive setup.
# Supports Linux, macOS, and WSL.
#
set -euo pipefail

REPO_URL="${ACKSTREET_REPO_URL:-https://github.com/ObadiaNgenoh/ackstreet-agent.git}"
INSTALL_DIR="${ACKSTREET_INSTALL_DIR:-$HOME/.ackstreet/src}"
VENV_DIR="${ACKSTREET_VENV_DIR:-$INSTALL_DIR/.venv}"
MIN_PYTHON="3.9"

# --- output helpers --------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"
  GREEN="$(printf '\033[32m')"; RED="$(printf '\033[31m')"
  YELLOW="$(printf '\033[33m')"; CYAN="$(printf '\033[36m')"
  RESET="$(printf '\033[0m')"
else
  BOLD=""; DIM=""; GREEN=""; RED=""; YELLOW=""; CYAN=""; RESET=""
fi

info()  { printf '%s\n' "${CYAN}==>${RESET} $*"; }
ok()    { printf '%s\n' "${GREEN}  ok${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}warn${RESET} $*"; }
die()   { printf '%s\n' "${RED}error${RESET} $*" >&2; exit 1; }

cat <<'BANNER'

   _   ___ _  __ ___ _____ ___ ___ _____   _   ___ ___ _  _ _____
  /_\ / __| |/ // __|_   _| _ \ __|_   _| /_\ / __| __| \| |_   _|
 / _ \ (__| ' < \__ \ | | |   / _|  | |  / _ \ (_ | _|| .` | | |
/_/ \_\___|_|\_\|___/ |_| |_|_\___| |_| /_/ \_\___|___|_|\_| |_|

BANNER

# --- 1. locate a suitable Python ------------------------------------------
info "Looking for Python ${MIN_PYTHON}+"
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  die "Python ${MIN_PYTHON}+ not found. Install it first:
       Debian/Ubuntu : sudo apt-get install -y python3 python3-venv python3-pip
       Fedora/RHEL   : sudo dnf install -y python3 python3-pip
       macOS         : brew install python@3.12
       Arch          : sudo pacman -S python"
fi
ok "$($PYTHON_BIN --version) at $PYTHON_BIN"

# --- 2. obtain the source --------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "")"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
  info "Installing from the local checkout at $SCRIPT_DIR"
  INSTALL_DIR="$SCRIPT_DIR"
  VENV_DIR="${ACKSTREET_VENV_DIR:-$INSTALL_DIR/.venv}"
else
  if [ -d "$INSTALL_DIR/.git" ]; then
    info "Updating existing checkout at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only || warn "could not update; using the existing checkout"
  else
    command -v git >/dev/null 2>&1 || die "git is required to clone the repository.
       Debian/Ubuntu : sudo apt-get install -y git
       macOS         : brew install git"
    info "Cloning into $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
fi

# --- 3. create the virtualenv ---------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
  info "Creating virtualenv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR" || die "could not create a virtualenv.
       On Debian/Ubuntu install the venv module: sudo apt-get install -y python3-venv"
else
  info "Reusing virtualenv at $VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$VENV_DIR/Scripts/python.exe"   # Windows/WSL layout
[ -x "$VENV_PY" ] || die "virtualenv looks broken at $VENV_DIR — delete it and re-run"

# --- 4. install the package ------------------------------------------------
info "Upgrading pip"
"$VENV_PY" -m pip install --quiet --upgrade pip setuptools wheel

info "Installing ACKSTREET AGENT and its dependencies"
"$VENV_PY" -m pip install --quiet -e "$INSTALL_DIR"

ACKSTREET_BIN="$VENV_DIR/bin/ackstreet"
[ -x "$ACKSTREET_BIN" ] || ACKSTREET_BIN="$VENV_DIR/Scripts/ackstreet.exe"
[ -x "$ACKSTREET_BIN" ] || die "the ackstreet entry point was not created"
ok "installed: $ACKSTREET_BIN"

INSTALL_BIN="$VENV_DIR/bin/ackstreet-install"
[ -x "$INSTALL_BIN" ] || INSTALL_BIN="$VENV_DIR/Scripts/ackstreet-install.exe"
[ -x "$INSTALL_BIN" ] || die "the ackstreet-install entry point was not created"
ok "installer: $INSTALL_BIN"

# --- 5. initialise config, skills and memory (non-interactive) -----------
info "Initialising configuration and directory layout"
"$ACKSTREET_BIN" init >/dev/null 2>&1 || true

# --- 6. make `ackstreet` reachable -----------------------------------------
BIN_DIR="$(dirname "$ACKSTREET_BIN")"
LINK_DIR="$HOME/.local/bin"
mkdir -p "$LINK_DIR"
ln -sf "$ACKSTREET_BIN" "$LINK_DIR/ackstreet" 2>/dev/null || true
ok "linked into $LINK_DIR/ackstreet"

case ":$PATH:" in
  *":$LINK_DIR:"*) ;;
  *)
    warn "$LINK_DIR is not on your PATH. Add it with:"
    printf '       %s\n' "echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
    ;;
esac

# --- 7. launch interactive onboarding (guided setup) ----------------------
echo
info "Starting interactive setup wizard"
echo

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            if [[ "$ID" == "ubuntu" || "$ID" == "debian" ]]; then
                echo "ubuntu"
            elif [[ "$ID" == "fedora" || "$ID" == "rhel" || "$ID" == "rocky" ]]; then
                echo "rhel"
            elif [[ "$ID" == "arch" ]]; then
                echo "arch"
            else
                echo "linux_generic"
            fi
        else
            echo "linux_generic"
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "darwin"
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        echo "windows_wsl"
    else
        echo "unknown"
    fi
}

OS=$(detect_os)

# Launch interactive onboarding
"$VENV_PY" -m ackstreet.installer.cli "$OS"
ONBOARDING_STATUS=$?

echo

if [ "$ONBOARDING_STATUS" -eq 0 ]; then
  printf '%s\n' "${GREEN}${BOLD}✓ ACKSTREET AGENT is installed and configured.${RESET}"
  echo
  cat <<EOF

${BOLD}Quick Start${RESET}

  1. Start chatting:
     ackstreet chat

  2. Run a single task:
     ackstreet run "your task here"

  3. Check system status:
     ackstreet doctor

${BOLD}Documentation${RESET}
  Config:  ~/.ackstreet/config.toml
  Skills:  ~/.ackstreet/skills/
  Memory:  ~/.ackstreet/memory/
  Logs:    ~/.ackstreet/logs/

${BOLD}Next: Connect a Messaging Gateway${RESET}
  ackstreet connect telegram
  ackstreet serve telegram

  or

  ackstreet connect whatsapp
  ackstreet serve whatsapp

EOF
  exit 0
else
  printf '%s\n' "${YELLOW}${BOLD}Setup was cancelled or encountered an error.${RESET}"
  printf '%s\n' "You can re-run setup at any time with: ackstreet-install"
  exit 1
fi
