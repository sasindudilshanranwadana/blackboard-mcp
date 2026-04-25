#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Blackboard MCP — One-shot installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sasindudilshanranwadana/blackboard-mcp/main/install.sh | bash
#
# What it does:
#   1. Checks Python 3.11+ is available
#   2. Clones the repo to ~/blackboard-mcp  (or updates if already installed)
#   3. Installs Python dependencies
#   4. Installs Playwright's Chromium browser
#   5. Runs the interactive setup wizard
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD="\033[1m"
DIM="\033[2m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

ok()   { echo -e "  ${GREEN}✅${RESET}  $*"; }
info() { echo -e "  ${DIM}ℹ${RESET}   $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET}   $*"; }
fail() { echo -e "  ${RED}✗${RESET}   $*"; exit 1; }
step() { echo -e "\n${CYAN}${BOLD}▶  $*${RESET}"; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║        Blackboard MCP  —  Installer          ║"
echo "  ║  Connect Claude AI to your university LMS    ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Config ────────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/sasindudilshanranwadana/blackboard-mcp.git"
INSTALL_DIR="${BLACKBOARD_MCP_DIR:-$HOME/blackboard-mcp}"

# ── Step 1: Check OS ─────────────────────────────────────────────────────────
step "Checking your system"

OS="$(uname -s)"
case "$OS" in
  Darwin)  ok "macOS detected" ;;
  Linux)   ok "Linux detected" ;;
  *)       warn "Untested OS: $OS — proceeding anyway" ;;
esac

# ── Step 2: Check Python ──────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major="${ver%%.*}"
    minor="${ver##*.}"
    if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
      PYTHON="$candidate"
      ok "Python $ver found  ($candidate)"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo ""
  fail "Python 3.11 or later is required but was not found.

  ${BOLD}Install options:${RESET}
    macOS:   brew install python@3.13
    Ubuntu:  sudo apt install python3.13
    Or download from: https://python.org/downloads"
fi

# ── Step 3: Check git ─────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  fail "git is required but was not found.

  ${BOLD}Install options:${RESET}
    macOS:  brew install git   (or install Xcode Command Line Tools)
    Ubuntu: sudo apt install git"
fi
ok "git found"

# ── Step 4: Clone or update ───────────────────────────────────────────────────
step "Setting up Blackboard MCP in ${BOLD}$INSTALL_DIR${RESET}"

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Already installed — pulling latest updates..."
  git -C "$INSTALL_DIR" pull --quiet
  ok "Updated to latest version"
else
  info "Cloning repository..."
  git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  ok "Repository cloned"
fi

cd "$INSTALL_DIR"

# ── Step 5: Python dependencies ───────────────────────────────────────────────
step "Installing Python dependencies"

# Prefer a venv inside the install dir to avoid system-package conflicts
VENV_DIR="$INSTALL_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
  info "Creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Virtual environment created"
fi

VENV_PYTHON="$VENV_DIR/bin/python3"
VENV_PIP="$VENV_DIR/bin/pip"

"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet -r requirements.txt
ok "Python packages installed"

# ── Step 6: Playwright Chromium ───────────────────────────────────────────────
step "Installing Playwright browser (Chromium)"

# Only install if not already present
if "$VENV_PYTHON" -c "from playwright.sync_api import sync_playwright; b=sync_playwright().__enter__().chromium; b.launch(headless=True).close()" &>/dev/null 2>&1; then
  ok "Chromium already installed"
else
  info "Downloading Chromium (this may take a minute)..."
  "$VENV_DIR/bin/playwright" install chromium --quiet
  ok "Chromium installed"
fi

# ── Step 7: Patch setup.py to use venv python ─────────────────────────────────
# Write a wrapper that always uses the venv python so Claude Desktop picks it up correctly
WRAPPER="$INSTALL_DIR/run_server.sh"
cat > "$WRAPPER" <<WRAPPER_EOF
#!/usr/bin/env bash
exec "$VENV_PYTHON" "$INSTALL_DIR/server.py" "\$@"
WRAPPER_EOF
chmod +x "$WRAPPER"

# ── Step 8: Run setup wizard ──────────────────────────────────────────────────
step "Launching setup wizard"
echo ""
info "The wizard will:"
info "  • Ask for your university's Blackboard URL"
info "  • Open a browser — log in as you normally would"
info "  • Configure Claude Desktop / Claude Code automatically"
echo ""

"$VENV_PYTHON" "$INSTALL_DIR/setup.py"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   🎉  Blackboard MCP installed & ready!      ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"
info "Installed to: ${BOLD}$INSTALL_DIR${RESET}"
info "To update later, re-run the installer or:"
info "  cd $INSTALL_DIR && git pull && $VENV_PYTHON setup.py"
info ""
info "To reset your session:"
info "  $VENV_PYTHON $INSTALL_DIR/setup.py --reset"
echo ""
