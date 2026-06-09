#!/usr/bin/env bash
# bootstrap.sh — idempotent Hostinger KVM2 setup for gammaqc-mesh.
#
# Run ONCE as root on a fresh Ubuntu 22.04+ box. Safe to re-run; every
# step checks for existing state before doing anything destructive.

set -euo pipefail
IFS=$'\n\t'

MESH_ROOT="/opt/gammaqc-mesh"
WEB_ROOT="/var/www/gammaqc-mesh"
NGINX_SNIPPETS="/etc/nginx/snippets"
NGINX_SITES_AVAIL="/etc/nginx/sites-available"
NGINX_SITES_ENAB="/etc/nginx/sites-enabled"
CERTBOT_WEBROOT="/var/www/certbot"

log()   { printf '\033[1m[bootstrap]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[bootstrap]\033[0m %s\n' "$*"; }
err()   { printf '\033[31m[bootstrap]\033[0m %s\n' "$*" >&2; }

if [ "$EUID" -ne 0 ]; then
    err "must run as root"; exit 1
fi
if [ ! -d "$MESH_ROOT" ]; then
    err "$MESH_ROOT not found — rsync the mesh/ directory there first"; exit 1
fi

log "1/8  apt update + install nginx + certbot"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx logrotate

log "2/8  drop the privacy log-format snippet into nginx http context"
install -d -m 0755 "$NGINX_SNIPPETS"
install -m 0644 "$MESH_ROOT/nginx/nginx.conf.snippet" "$NGINX_SNIPPETS/gammaqc-mesh.conf"
# Ensure the snippet is included from the main config (idempotent)
if ! grep -q "include $NGINX_SNIPPETS/gammaqc-mesh.conf" /etc/nginx/nginx.conf; then
    sed -i '/^http {$/a\    include '"$NGINX_SNIPPETS"'/gammaqc-mesh.conf;' /etc/nginx/nginx.conf
    log "  ↳ added include directive to /etc/nginx/nginx.conf"
fi

log "3/8  create web root + certbot webroot"
install -d -m 0755 "$WEB_ROOT"
install -d -m 0755 "$CERTBOT_WEBROOT"

log "4/8  configure logrotate (7-day retention, no PII)"
cat > /etc/logrotate.d/gammaqc-mesh <<'EOF'
/var/log/nginx/gammaqc-mesh*.log {
    daily
    rotate 7
    missingok
    notifempty
    compress
    delaycompress
    create 0640 www-data adm
    sharedscripts
    postrotate
        [ -f /run/nginx.pid ] && kill -USR1 $(cat /run/nginx.pid) || true
    endscript
}
EOF

log "5/8  render per-domain nginx configs + landing pages"
bash "$MESH_ROOT/scripts/render-configs.sh"

log "6/8  test nginx config (FAILS HARD if any rendered domain block is invalid)"
nginx -t

log "7/8  reload nginx (HTTP-only at this point; certs come next)"
systemctl reload nginx

log "8/8  certbot auto-renewal timer (already systemd-default on Ubuntu 22.04, just confirm)"
systemctl enable --now certbot.timer

log "DONE. Next:"
log "  - Verify all 500 domains' DNS A records point at this box: dig +short <DOMAIN> @1.1.1.1"
log "  - Then run: bash $MESH_ROOT/scripts/certbot-bulk.sh"
log "  - Then run: bash $MESH_ROOT/scripts/verify-mesh.sh"
