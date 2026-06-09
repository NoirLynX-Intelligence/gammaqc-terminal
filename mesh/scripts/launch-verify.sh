#!/usr/bin/env bash
# launch-verify.sh — comprehensive Wave 1 readiness check.
#
# Run this IMMEDIATELY before posting the first Wave 1 launch post.
# Tests the entire install vector end-to-end + samples the mesh.
# Exits 0 on all-green (safe to post); exits 1 on any failure.
#
# Designed to take ~30 seconds. Run it from any shell with curl + python3.

set -uo pipefail
IFS=$'\n\t'

CANONICAL="${CANONICAL:-install.gammaqc.com}"
SAMPLE_MESH_NODES=(
    "1333000.xyz" "1333100.xyz" "1333250.xyz" "1333500.xyz"
    "1333750.xyz" "1333900.xyz" "1333998.xyz"
)
PYPI_PACKAGE="gammaqc-terminal"
PYPI_EXPECTED_VERSION="${PYPI_EXPECTED_VERSION:-0.2.0}"
TIMEOUT="${TIMEOUT:-10}"

pass=0
fail=0
warn=0
checks=()

ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; pass=$((pass + 1)); }
nope() { printf '\033[31m✗\033[0m %s\n' "$*"; fail=$((fail + 1)); checks+=("FAIL: $*"); }
huh()  { printf '\033[33m~\033[0m %s\n' "$*"; warn=$((warn + 1)); }

section() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

# ─── 1. PyPI package available + correct version ─────────────────────────────
section "PyPI package"

pypi_resp="$(curl -fsS -m $TIMEOUT "https://pypi.org/pypi/${PYPI_PACKAGE}/json" 2>&1 || echo '')"
if [ -z "$pypi_resp" ]; then
    nope "PyPI: ${PYPI_PACKAGE} fetch failed (network or PyPI outage)"
else
    actual_ver="$(printf '%s' "$pypi_resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])' 2>/dev/null || echo unknown)"
    if [ "$actual_ver" = "$PYPI_EXPECTED_VERSION" ]; then
        ok "PyPI ${PYPI_PACKAGE} version: ${actual_ver}"
    else
        huh "PyPI ${PYPI_PACKAGE} version: ${actual_ver} (expected ${PYPI_EXPECTED_VERSION})"
    fi
fi

# ─── 2. Canonical install endpoint ───────────────────────────────────────────
section "Canonical install endpoint (${CANONICAL})"

healthz_body="$(curl -fsS -m $TIMEOUT "https://${CANONICAL}/healthz" 2>&1 || echo '')"
if [ "$healthz_body" = "ok" ]; then
    ok "GET https://${CANONICAL}/healthz → 'ok'"
else
    nope "GET https://${CANONICAL}/healthz → '${healthz_body}' (expected 'ok')"
fi

landing="$(curl -fsS -m $TIMEOUT "https://${CANONICAL}/" 2>&1 || echo '')"
if printf '%s' "$landing" | grep -q "gammaqc-terminal"; then
    ok "GET https://${CANONICAL}/ → landing page rendered (brand present)"
else
    nope "GET https://${CANONICAL}/ → no 'gammaqc-terminal' in body"
fi

install_hdrs="$(curl -fsSI -m $TIMEOUT "https://${CANONICAL}/install" 2>&1 || echo '')"
if printf '%s' "$install_hdrs" | grep -qi 'content-type:.*x-shellscript'; then
    ok "GET https://${CANONICAL}/install → text/x-shellscript content-type"
else
    nope "GET https://${CANONICAL}/install → wrong content-type"
fi

# /install body sanity — must start with bash shebang
install_body="$(curl -fsS -m $TIMEOUT "https://${CANONICAL}/install" 2>&1 || echo '')"
if printf '%s' "$install_body" | head -1 | grep -q '^#!/usr/bin/env bash'; then
    ok "GET https://${CANONICAL}/install → valid bash shebang"
else
    nope "GET https://${CANONICAL}/install → first line is not '#!/usr/bin/env bash'"
fi

sbom="$(curl -fsS -m $TIMEOUT "https://${CANONICAL}/sbom" 2>&1 || echo '')"
if printf '%s' "$sbom" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["package"]=="gammaqc-terminal"; print("ok")' 2>/dev/null | grep -q ok; then
    ok "GET https://${CANONICAL}/sbom → valid JSON, package=gammaqc-terminal"
else
    nope "GET https://${CANONICAL}/sbom → invalid JSON or wrong package field"
fi

# ─── 3. Mesh sample — each should 301 to canonical ───────────────────────────
section "Mesh redirect sample (7 nodes across range)"

for d in "${SAMPLE_MESH_NODES[@]}"; do
    hdrs="$(curl -fsSI --max-redirs 0 -m $TIMEOUT "https://${d}/install" 2>&1 || echo '')"
    status_line="$(printf '%s' "$hdrs" | head -1 | tr -d '\r')"
    location="$(printf '%s' "$hdrs" | grep -i '^Location:' | head -1 | tr -d '\r' | cut -d: -f2- | sed 's/^ *//')"
    if printf '%s' "$status_line" | grep -q "301"; then
        if printf '%s' "$location" | grep -q "install.gammaqc.com"; then
            ok "${d} → 301 → ${location}"
        else
            nope "${d} → 301 → ${location} (expected install.gammaqc.com)"
        fi
    else
        huh "${d} → ${status_line:-no response} (timeout or non-301; verify manually)"
    fi
done

# ─── 4. PyPI install simulation (optional, if pip available) ────────────────
section "PyPI install (dry-run)"

if command -v pip3 >/dev/null 2>&1; then
    if pip3 download "${PYPI_PACKAGE}==${PYPI_EXPECTED_VERSION}" -d /tmp/gammaqc-verify --no-deps -q 2>&1 | tail -3; then
        ok "pip3 download ${PYPI_PACKAGE}==${PYPI_EXPECTED_VERSION} succeeded"
        rm -rf /tmp/gammaqc-verify
    else
        nope "pip3 download failed — package may not be installable cleanly"
    fi
else
    huh "pip3 not available — skipping install simulation"
fi

# ─── 5. DNS check on canonical ───────────────────────────────────────────────
section "DNS resolution (cross-check)"

if command -v nslookup >/dev/null 2>&1; then
    resolved="$(nslookup "$CANONICAL" 1.1.1.1 2>&1 | grep -E '^Address: [0-9]' | tail -1 | awk '{print $2}')"
    if [ -n "$resolved" ]; then
        ok "${CANONICAL} resolves via 1.1.1.1 to: ${resolved}"
    else
        nope "${CANONICAL} does NOT resolve via 1.1.1.1"
    fi
fi

# ─── Verdict ─────────────────────────────────────────────────────────────────
echo
section "Pre-launch verdict"
echo "  passed: $pass"
echo "  failed: $fail"
echo "  warnings (non-blocking): $warn"
echo

if [ $fail -eq 0 ]; then
    printf '\033[1;32m✓✓✓ GREEN — safe to post Wave 1 ✓✓✓\033[0m\n'
    echo
    echo "Recommended first post: marketing/launch/01-reddit-algotrading.md"
    echo "                 (then) marketing/launch/03-hackernews-show.md"
    echo "        (then over 2wk) marketing/launch/02-fintwit-threads.md"
    echo "                 (then) marketing/launch/04-stocktwits.md"
    exit 0
fi

printf '\033[1;31m✗✗✗ RED — DO NOT POST ✗✗✗\033[0m\n'
echo
echo "Failures:"
for c in "${checks[@]}"; do echo "  - $c"; done
exit 1
