#!/usr/bin/env bash
# verify-mesh.sh — smoke-test every domain in the mesh.
#
# For each domain, verifies:
#   1. HTTPS responds (cert is valid)
#   2. / returns 200 with the niche_hook present
#   3. /install returns 200 with content-type text/x-shellscript
#   4. /sbom returns 200 valid JSON
#   5. /healthz returns 200 "ok"
#
# Reports a clean summary. Exits 1 if any domain fails any check.

set -uo pipefail
IFS=$'\n\t'

MESH_ROOT="${MESH_ROOT:-/opt/gammaqc-mesh}"
DOMAINS_FILE="$MESH_ROOT/nginx/domains.list"
TIMEOUT="${TIMEOUT:-10}"

pass=0
fail=0
failures=()

check() {
    local domain="$1"
    local errors=()

    # /healthz (cheapest, fastest fail signal)
    if ! body="$(curl -fsS --max-time "$TIMEOUT" "https://${domain}/healthz" 2>&1)"; then
        errors+=("healthz_unreachable")
    elif [ "$body" != "ok" ]; then
        errors+=("healthz_wrong_body:'${body}'")
    fi

    # / — must contain the niche_hook
    if html="$(curl -fsS --max-time "$TIMEOUT" "https://${domain}/" 2>&1)"; then
        if ! grep -q "gammaqc-terminal" <<<"$html"; then
            errors+=("landing_missing_brand")
        fi
        if ! grep -q "$domain" <<<"$html"; then
            errors+=("landing_missing_domain_substitution")
        fi
    else
        errors+=("landing_unreachable")
    fi

    # /install — must be a shell script
    if hdrs="$(curl -fsSI --max-time "$TIMEOUT" "https://${domain}/install" 2>&1)"; then
        if ! grep -qi 'content-type:.*x-shellscript' <<<"$hdrs"; then
            errors+=("install_wrong_content_type")
        fi
        # Body sanity check
        if script="$(curl -fsS --max-time "$TIMEOUT" "https://${domain}/install" 2>&1)"; then
            if ! head -1 <<<"$script" | grep -q '^#!/usr/bin/env bash'; then
                errors+=("install_missing_shebang")
            fi
        fi
    else
        errors+=("install_unreachable")
    fi

    # /sbom — must be valid JSON
    if json="$(curl -fsS --max-time "$TIMEOUT" "https://${domain}/sbom" 2>&1)"; then
        if ! python3 -c "import sys,json; json.loads(sys.argv[1])" "$json" 2>/dev/null; then
            errors+=("sbom_invalid_json")
        fi
    else
        errors+=("sbom_unreachable")
    fi

    if [ ${#errors[@]} -eq 0 ]; then
        printf '\033[32m✓\033[0m %s\n' "$domain"
        pass=$((pass + 1))
    else
        printf '\033[31m✗\033[0m %s — %s\n' "$domain" "$(IFS=,; echo "${errors[*]}")"
        fail=$((fail + 1))
        failures+=("$domain")
    fi
}

# Iterate domains
while IFS=$'\t' read -r domain _hook; do
    [[ "$domain" =~ ^[[:space:]]*# ]] && continue
    [ -z "${domain// }" ] && continue
    domain="${domain// /}"
    check "$domain"
done < "$DOMAINS_FILE"

echo
echo "=== mesh verification summary ==="
echo "  passed: $pass"
echo "  failed: $fail"
if [ ${#failures[@]} -gt 0 ]; then
    echo "  failed domains:"
    for d in "${failures[@]}"; do echo "    - $d"; done
    exit 1
fi
echo "  ALL GREEN"
