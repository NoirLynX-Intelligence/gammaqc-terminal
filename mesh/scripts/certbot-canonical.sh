#!/usr/bin/env bash
# certbot-canonical.sh — issue the Let's Encrypt cert for the canonical
# install endpoint only.
#
# v0.3 simplification: instead of certbot-bulk.sh trying to issue 1,500
# LE certs (which takes 30 weeks at LE's 50/week rate-limit), we issue
# ONE cert for install.gammaqc.com. The mesh's ~1,500 numeric .xyz
# domains stay HTTP-only redirects — they 301 to https://install.gammaqc.com
# which terminates TLS with this single cert.
#
# When/if we want HTTPS at the mesh edge later (e.g. for users typing
# https://1334077.xyz directly), we can run a separate certbot-mesh.sh
# in 50/week batches. For launch, this is enough.

set -euo pipefail
IFS=$'\n\t'

CANONICAL_HOST="${CANONICAL_HOST:-install.gammaqc.com}"
ACME_EMAIL="${ACME_EMAIL:-ops@gammaqc.com}"
CERTBOT_WEBROOT="/var/www/certbot"
MESH_ROOT="${MESH_ROOT:-/opt/gammaqc-mesh}"

if [ "$EUID" -ne 0 ]; then
    echo "must run as root"; exit 1
fi

# Pre-flight: DNS must resolve to this box. Without this, certbot validates
# fails with HTTP 404 from whichever IP the domain actually resolves to,
# burning a rate-limit slot.
resolved_ip="$(dig +short "$CANONICAL_HOST" @1.1.1.1 2>/dev/null | head -1)"
my_ip="$(curl -fsS --max-time 5 https://ifconfig.io 2>/dev/null || echo '')"
if [ -z "$resolved_ip" ]; then
    echo "::error::DNS for ${CANONICAL_HOST} doesn't resolve. Set an A record"
    echo "          pointing to this box (${my_ip:-<unknown>}) at the registrar."
    exit 1
fi
if [ -n "$my_ip" ] && [ "$resolved_ip" != "$my_ip" ]; then
    echo "::warning::DNS for ${CANONICAL_HOST} resolves to ${resolved_ip}"
    echo "           but this box is ${my_ip}. Proceeding anyway — certbot"
    echo "           will fail validation if the IPs really differ."
fi

echo "[certbot-canonical] issuing cert for ${CANONICAL_HOST}…"
certbot certonly --webroot \
    -w "$CERTBOT_WEBROOT" \
    -d "$CANONICAL_HOST" \
    --non-interactive \
    --agree-tos \
    --email "$ACME_EMAIL" \
    --no-eff-email \
    --keep-until-expiring

echo "[certbot-canonical] re-rendering nginx with post-cert template…"
bash "$MESH_ROOT/scripts/render-configs.sh"

echo "[certbot-canonical] validating + reloading nginx…"
nginx -t
systemctl reload nginx

echo "[certbot-canonical] ✓ ${CANONICAL_HOST} now serves HTTPS"
echo "[certbot-canonical] smoke test:"
echo "    curl -sI https://${CANONICAL_HOST}/healthz"
