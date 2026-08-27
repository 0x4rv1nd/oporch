#!/usr/bin/env bash
# oporch installer — https://github.com/0x4rv1nd/oporch
# Usage: curl -fsSL https://raw.githubusercontent.com/0x4rv1nd/oporch/master/install.sh | bash
set -euo pipefail

REPO="https://github.com/0x4rv1nd/oporch"
PYPI_NAME="oporch"
MIN_PYTHON="3.12"

# ── colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[oporch]${RESET} $*"; }
success() { echo -e "${GREEN}[oporch] ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}[oporch] ⚠${RESET} $*"; }
die()     { echo -e "${RED}[oporch] ✗${RESET} $*" >&2; exit 1; }

# ── header ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ⚡ oporch installer"
echo "  Multi-Agent Orchestration System for OpenCode"
echo -e "${RESET}"

# ── python version check ──────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done
[ -z "$PYTHON" ] && die "Python ${MIN_PYTHON}+ is required. Install it from https://python.org"
info "Python $($PYTHON --version) ✓"

# ── prerequisite: opencode ────────────────────────────────────────────────────
if ! command -v opencode &>/dev/null; then
    warn "opencode CLI not found. Install it from https://opencode.ai before using oporch."
fi

# ── pick installer ────────────────────────────────────────────────────────────
if command -v uv &>/dev/null; then
    INSTALLER="uv"
elif command -v pipx &>/dev/null; then
    INSTALLER="pipx"
else
    INSTALLER="pip"
fi
info "Using installer: ${BOLD}${INSTALLER}${RESET}"

# ── install ───────────────────────────────────────────────────────────────────
case "$INSTALLER" in
    uv)
        uv tool install "$PYPI_NAME" || die "uv install failed"
        ;;
    pipx)
        pipx install "$PYPI_NAME" || die "pipx install failed"
        ;;
    pip)
        warn "Neither uv nor pipx found. Installing with pip (consider using pipx for cleaner installs)."
        "$PYTHON" -m pip install --user "$PYPI_NAME" || die "pip install failed"
        ;;
esac

# ── verify ────────────────────────────────────────────────────────────────────
if command -v oporch &>/dev/null; then
    success "oporch installed successfully!"
    echo ""
    echo -e "  ${BOLD}Get started:${RESET}"
    echo "    cd your-project"
    echo "    oporch"
    echo ""
    echo -e "  ${BOLD}Optional (token compression):${RESET}"
    echo "    pip install \"headroom-ai[all]\""
    echo "    headroom wrap opencode"
    echo "    oporch"
    echo ""
    echo -e "  Docs: ${CYAN}${REPO}${RESET}"
else
    warn "oporch command not found in PATH after install."
    warn "You may need to add the user/tool bin directory to your PATH."
    warn "Try: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
