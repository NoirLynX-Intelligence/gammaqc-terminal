#!/usr/bin/env bash
# render-configs.sh — expand the templates into per-domain artifacts.
#
# Reads:   $MESH_ROOT/nginx/gammaqc-mesh.conf (template)
#          $MESH_ROOT/nginx/domains.list      (domain + niche_hook list)
#          $MESH_ROOT/landing/index.html      (landing template)
#          $MESH_ROOT/landing/install         (the curl-pipe-bash script)
#
# Writes:  /etc/nginx/sites-available/<domain>.conf  (one per domain)
#          /etc/nginx/sites-enabled/<domain>.conf    (symlinks)
#          /var/www/gammaqc-mesh/<domain>/index.html
#          /var/www/gammaqc-mesh/<domain>/install
#          /var/www/gammaqc-mesh/<domain>/sbom.json

set -euo pipefail
IFS=$'\n\t'

MESH_ROOT="${MESH_ROOT:-/opt/gammaqc-mesh}"
WEB_ROOT="/var/www/gammaqc-mesh"
SITES_AVAIL="/etc/nginx/sites-available"
SITES_ENAB="/etc/nginx/sites-enabled"

TEMPLATE_NGINX="$MESH_ROOT/nginx/gammaqc-mesh.conf"
TEMPLATE_LANDING="$MESH_ROOT/landing/index.html"
INSTALL_SCRIPT="$MESH_ROOT/landing/install"
DOMAINS_FILE="$MESH_ROOT/nginx/domains.list"

[ -f "$TEMPLATE_NGINX" ]   || { echo "missing $TEMPLATE_NGINX"; exit 1; }
[ -f "$TEMPLATE_LANDING" ] || { echo "missing $TEMPLATE_LANDING"; exit 1; }
[ -f "$INSTALL_SCRIPT" ]   || { echo "missing $INSTALL_SCRIPT"; exit 1; }
[ -f "$DOMAINS_FILE" ]     || { echo "missing $DOMAINS_FILE"; exit 1; }

# Resolve published gammaqc-terminal version (for the SBOM). Fall back to
# 'unknown' if PyPI is unreachable — never fail the render on network glitch.
PKG_VER="$(curl -fsS --max-time 5 'https://pypi.org/pypi/gammaqc-terminal/json' 2>/dev/null \
            | python3 -c 'import sys,json; print(json.load(sys.stdin)["info"]["version"])' \
            2>/dev/null || echo unknown)"
RENDER_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

count=0
while IFS=$'\t' read -r domain niche_hook; do
    # Skip comments + blank lines
    [[ "$domain" =~ ^[[:space:]]*# ]] && continue
    [ -z "${domain// }" ] && continue
    # Strip whitespace
    domain="${domain// /}"
    niche_hook="${niche_hook## }"

    # 1. nginx server block — template expansion via envsubst-style sed
    sed -e "s|{{DOMAIN}}|${domain}|g" \
        "$TEMPLATE_NGINX" > "$SITES_AVAIL/${domain}.conf"
    ln -sf "$SITES_AVAIL/${domain}.conf" "$SITES_ENAB/${domain}.conf"

    # 2. landing page
    install -d -m 0755 "$WEB_ROOT/${domain}"
    sed -e "s|{{DOMAIN}}|${domain}|g" \
        -e "s|{{NICHE_HOOK}}|${niche_hook}|g" \
        "$TEMPLATE_LANDING" > "$WEB_ROOT/${domain}/index.html"

    # 3. install script (byte-identical across domains)
    install -m 0644 "$INSTALL_SCRIPT" "$WEB_ROOT/${domain}/install"

    # 4. SBOM — small JSON manifest describing what gets installed
    cat > "$WEB_ROOT/${domain}/sbom.json" <<EOF
{
  "package": "gammaqc-terminal",
  "version_pinned": "${PKG_VER}",
  "source": "PyPI",
  "install_command": "curl -sL https://${domain}/install | bash",
  "install_method": "pipx (preferred) or pip3 --user (fallback)",
  "telemetry": false,
  "third_party_calls_during_install": ["pypi.org"],
  "license": "Apache-2.0",
  "source_code": "https://github.com/NoirLynX-Intelligence/gammaqc-terminal",
  "rendered_at": "${RENDER_TS}",
  "mesh_domain": "${domain}"
}
EOF

    count=$((count + 1))
done < "$DOMAINS_FILE"

# Set perms for nginx user
chown -R www-data:www-data "$WEB_ROOT"

echo "[render-configs] rendered $count domains"
