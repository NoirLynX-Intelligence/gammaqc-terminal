#!/usr/bin/env bash
# certbot-bulk.sh — obtain LE certs for the entire mesh in rate-limit-
# friendly batches.
#
# Let's Encrypt rate limits per public-suffix:
#   - 50 certs per registered domain per week (we register ONE per cert)
#   - 5 duplicate certs per week
#   - 100 names per cert (we use one-name-per-cert for blast-radius isolation)
#
# Strategy:
#   - Batch into chunks of 40 (under the 50/wk limit) per script run
#   - Use webroot challenge (--webroot) so Nginx stays up the whole time
#   - Skip domains we already have certs for (idempotent)
#   - Email = ops@gammaqc.com for LE expiry warnings

set -euo pipefail
IFS=$'\n\t'

MESH_ROOT="${MESH_ROOT:-/opt/gammaqc-mesh}"
DOMAINS_FILE="$MESH_ROOT/nginx/domains.list"
CERTBOT_WEBROOT="/var/www/certbot"
ACME_EMAIL="${ACME_EMAIL:-ops@gammaqc.com}"
BATCH_SIZE="${BATCH_SIZE:-40}"

if [ "$EUID" -ne 0 ]; then
    echo "must run as root"; exit 1
fi

# Build the list of domains that DON'T have a cert yet
needs_cert=()
while IFS=$'\t' read -r domain _hook; do
    [[ "$domain" =~ ^[[:space:]]*# ]] && continue
    [ -z "${domain// }" ] && continue
    domain="${domain// /}"
    if [ ! -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]; then
        needs_cert+=("$domain")
    fi
done < "$DOMAINS_FILE"

if [ ${#needs_cert[@]} -eq 0 ]; then
    echo "[certbot-bulk] all domains already have certs — nothing to do"
    exit 0
fi

echo "[certbot-bulk] ${#needs_cert[@]} domain(s) need certs"
echo "[certbot-bulk] processing in batches of $BATCH_SIZE (LE rate-limit floor)"

batch_count=0
i=0
while [ $i -lt ${#needs_cert[@]} ]; do
    batch=("${needs_cert[@]:i:BATCH_SIZE}")
    batch_count=$((batch_count + 1))
    echo "--- batch ${batch_count} (${#batch[@]} domains) ---"

    for d in "${batch[@]}"; do
        # Pre-flight: confirm the domain RESOLVES to this box. Skip if not.
        # Without this, certbot tries to validate, LE hits a 404, and we
        # burn a rate-limit slot for nothing.
        resolved_ip="$(dig +short "$d" @1.1.1.1 2>/dev/null | head -1)"
        my_ip="$(curl -fsS --max-time 5 https://ifconfig.io 2>/dev/null || echo '')"
        if [ -n "$my_ip" ] && [ "$resolved_ip" != "$my_ip" ]; then
            echo "  skip ${d}: DNS resolves to '${resolved_ip}', this box is '${my_ip}'"
            continue
        fi

        echo "  certbot ${d}"
        if certbot certonly --webroot \
            -w "$CERTBOT_WEBROOT" \
            -d "$d" \
            --non-interactive \
            --agree-tos \
            --email "$ACME_EMAIL" \
            --no-eff-email \
            --keep-until-expiring; then
            # Cert issued — flip this domain's nginx config from
            # pre-cert (HTTP-only) to post-cert (HTTP→HTTPS + HTTPS)
            # by re-running render-configs (which now sees the cert
            # file and chooses the full template).
            echo "  ✓ ${d}: cert issued; re-rendering nginx with HTTPS block"
            bash "$MESH_ROOT/scripts/render-configs.sh" >/dev/null
            # Validate config before reload — never break a working
            # mesh because one domain's template render had an issue.
            if nginx -t 2>/dev/null; then
                systemctl reload nginx
            else
                echo "  ! ${d}: nginx -t failed after re-render; not reloading"
                nginx -t 2>&1 | sed 's/^/      /'
            fi
        else
            echo "  ! ${d}: certbot failed (rate limit? DNS?); continuing"
        fi
    done

    # If there's another batch coming, sleep 60s between batches to be
    # polite to LE (cooperative throttling beyond the formal rate limit)
    i=$((i + BATCH_SIZE))
    if [ $i -lt ${#needs_cert[@]} ]; then
        echo "  ↳ sleeping 60s before next batch"
        sleep 60
    fi
done

# Reload nginx so it picks up the new certs without restart
echo "[certbot-bulk] reloading nginx"
nginx -t && systemctl reload nginx

echo "[certbot-bulk] done"
