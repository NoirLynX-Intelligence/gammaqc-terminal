#!/usr/bin/env bash
# gammaqc-terminal — one-line install
#
# Curl-pipe-bash flow:
#   curl -sL https://<one-of-500-domains>.xyz/install | bash
#
# Behavior:
#   1. Detects pipx (preferred) or pip3
#   2. Installs gammaqc-terminal from PyPI (or the GAMMAQC_INSTALL_URL
#      override if running from a sovereign mesh node)
#   3. Verifies `gamma --help` is on PATH
#
# Privacy contract: no telemetry. The only network call this script
# makes is the package download (pip/pipx → PyPI or the override URL).
# We do not phone home to any GammaQC server. If you want analytics, opt
# in explicitly with `gamma login --api-key`.

set -euo pipefail

INSTALL_URL="${GAMMAQC_INSTALL_URL:-gammaqc-terminal}"
PKG_NAME="gammaqc-terminal"

color_red()    { printf '\033[31m%s\033[0m\n' "$*"; }
color_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
color_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
color_dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

bold "▌ GammaQC Terminal — Sovereign Quant CLI"
color_dim "  Free at the surface. PQC-sealed at the wall."
echo

# 1. Find a python install
if ! command -v python3 >/dev/null 2>&1; then
    color_red "✗ python3 not found on PATH."
    echo  "  Install Python 3.10+ from https://www.python.org/downloads/ and re-run."
    exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print("{}.{}".format(sys.version_info.major, sys.version_info.minor))')"
PY_MAJOR="$(printf "%s" "$PY_VERSION" | cut -d. -f1)"
PY_MINOR="$(printf "%s" "$PY_VERSION" | cut -d. -f2)"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    color_red "✗ Python 3.10+ required (found $PY_VERSION)."
    exit 1
fi
color_green "✓ Python $PY_VERSION detected"

# 2. Prefer pipx (isolated venv, won't trash system packages)
if command -v pipx >/dev/null 2>&1; then
    color_dim "  Using pipx (isolated install)"
    pipx install --force "$INSTALL_URL"
elif command -v pip3 >/dev/null 2>&1; then
    color_yellow "⚠ pipx not found; falling back to pip3 --user"
    color_dim "  Recommended: install pipx via 'python3 -m pip install --user pipx'"
    pip3 install --user --upgrade "$INSTALL_URL"
else
    color_red "✗ Neither pipx nor pip3 found. Install one and re-run."
    exit 1
fi

# 3. Verify
if command -v gamma >/dev/null 2>&1; then
    color_green "✓ gammaqc-terminal installed"
    echo
    bold "▌ Quick start"
    echo "  gamma analyze NVDA"
    echo "  gamma shock --portfolio ./holdings.csv --event 'Fed raises rates 50bps'"
    echo "  gamma watch NVDA --trigger 'pct_change < -3.0'"
    echo
    color_dim "  Pro features (10-Seat Council, PQC receipts):"
    color_dim "    gamma login --api-key <KEY>  (get one at https://gammaqc.com/pro)"
else
    color_yellow "⚠ Install succeeded but 'gamma' is not on PATH."
    echo  "  You may need to add your pipx/pip user bin dir to PATH."
    echo  "  pipx default:  ~/.local/bin  (macOS/Linux)  or  %USERPROFILE%\\.local\\bin (Windows)"
    echo  "  Then re-open your shell and run: gamma --help"
    exit 2
fi
