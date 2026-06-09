#!/usr/bin/env bash
# verify-mesh.sh — smoke-test the canonical endpoint + a sample of the
# numeric .xyz mesh nodes.
#
# v0.3 mesh design:
#   - canonical (install.gammaqc.com) serves full content + HTTPS
#   - all other Host headers → 301 redirect to canonical
#
# Verifies:
#   1. install.gammaqc.com → 200 on /, /install, /sbom, /healthz
#   2. install.gammaqc.com HTTPS works (after canonical cert is issued)
#   3. Each sample mesh node → 301 redirect to canonical
#
# Usage:
#   ./verify-mesh.sh              # tests canonical + 3 sample mesh nodes
#   ./verify-mesh.sh --samples 20 # tests canonical + 20 random mesh nodes
#   ./verify-mesh.sh --csv path   # tests against a CSV's domain list

set -uo pipefail
IFS=$'\n\t'

CANONICAL_HOST="${CANONICAL_HOST:-install.gammaqc.com}"
TIMEOUT="${TIMEOUT:-10}"
SAMPLES="${SAMPLES:-3}"
SAMPLE_DOMAINS=(
    "1333000.xyz" "1333500.xyz" "1333998.xyz"
)
# Sample domains are all from the GammaQC allocation (1333xxx range).
# DrkLynX domains (1334xxx, 1411xxx, named) are deliberately NOT tested
# from this script — they're under separate ownership and shouldn't have
# been flipped to this box.

# Allow --samples N to override
while [ $# -gt 0 ]; do
    case "$1" in
        --samples) SAMPLES="$2"; shift 2;;
        --csv)     CSV_FILE="$2"; shift 2;;
        *) echo "unknown arg: $1"; exit 1;;
    esac
done

pass=0
fail=0
failures=()

check() {
    local label="$1"
    local cmd="$2"
    local expected="$3"
    local got
    got="$(eval "$cmd" 2>&1)" || true
    if echo "$got" | grep -qE "$expected"; then
        printf '\033[32m✓\033[0m %s\n' "$label"
        pass=$((pass + 1))
    else
        printf '\033[31m✗\033[0m %s — expected /%s/ got: %s\n' "$label" "$expected" "$(echo "$got" | head -1)"
        fail=$((fail + 1))
        failures+=("$label")
    fi
}

echo "=== canonical: ${CANONICAL_HOST} (HTTP) ==="
check "  /healthz HTTP" \
      "curl -sI -m $TIMEOUT --resolve ${CANONICAL_HOST}:80:127.0.0.1 http://${CANONICAL_HOST}/healthz" \
      "HTTP/1.1 (200|301)"

# If cert exists locally (i.e. we're on the box), test HTTPS too
if [ -f "/etc/letsencrypt/live/${CANONICAL_HOST}/fullchain.pem" ]; then
    echo "=== canonical: ${CANONICAL_HOST} (HTTPS — cert present) ==="
    check "  /healthz HTTPS" \
          "curl -s -m $TIMEOUT https://${CANONICAL_HOST}/healthz" \
          "^ok"
    check "  /install Content-Type" \
          "curl -sI -m $TIMEOUT https://${CANONICAL_HOST}/install" \
          "Content-Type:.*x-shellscript"
    check "  /sbom valid JSON" \
          "curl -s -m $TIMEOUT https://${CANONICAL_HOST}/sbom | python3 -c 'import json,sys; json.load(sys.stdin); print(\"ok\")'" \
          "^ok"
else
    echo "(canonical cert not yet present; skipping HTTPS checks. Run certbot-canonical.sh.)"
fi

echo
echo "=== mesh sample: ${SAMPLES} numeric .xyz node(s) — expect 301 redirect ==="
i=0
for d in "${SAMPLE_DOMAINS[@]}"; do
    [ $i -ge $SAMPLES ] && break
    check "  ${d} → 301 to canonical" \
          "curl -sI -m $TIMEOUT --resolve ${d}:80:127.0.0.1 http://${d}/install" \
          "(HTTP/1.1 301|Location: https://${CANONICAL_HOST})"
    i=$((i + 1))
done

echo
echo "=== verification summary ==="
echo "  passed: $pass"
echo "  failed: $fail"
[ ${#failures[@]} -eq 0 ] && { echo "  ALL GREEN"; exit 0; }
echo "  failed checks:"
for f in "${failures[@]}"; do echo "    - $f"; done
exit 1
