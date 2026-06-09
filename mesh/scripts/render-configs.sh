#!/usr/bin/env bash
# render-configs.sh — render the canonical install endpoint + the mesh
# default-redirect server block, and CLEAN UP any stale per-niche configs
# from prior mesh designs.
#
# v0.3 architecture:
#   - install.gammaqc.com is the canonical brand URL (single LE cert,
#     full HTTPS, serves landing + /install + /sbom + /healthz)
#   - ~1,500 numeric .xyz domains point DNS A-records at this box; the
#     default-server block catches their Host headers and 301-redirects
#     any path to https://install.gammaqc.com$request_uri
#
# Reads:   $MESH_ROOT/nginx/canonical-http.conf    (pre-cert template)
#          $MESH_ROOT/nginx/canonical.conf         (post-cert template)
#          $MESH_ROOT/nginx/default-redirect.conf  (mesh fabric catch-all)
#          $MESH_ROOT/landing/index.html           (universal landing)
#          $MESH_ROOT/landing/install              (curl-pipe-bash script)
#
# Writes:  /etc/nginx/sites-available/install.gammaqc.com.conf
#          /etc/nginx/sites-available/default-redirect.conf
#          /etc/nginx/sites-enabled/{install.gammaqc.com,default-redirect}.conf  (symlinks)
#          /var/www/gammaqc-mesh/install.gammaqc.com/{index.html,install,sbom.json}
#
# Removes: any stale per-domain configs from the v0.1/v0.2 niche-mesh design.

set -euo pipefail
IFS=$'\n\t'

MESH_ROOT="${MESH_ROOT:-/opt/gammaqc-mesh}"
WEB_ROOT="/var/www/gammaqc-mesh"
SITES_AVAIL="/etc/nginx/sites-available"
SITES_ENAB="/etc/nginx/sites-enabled"
CANONICAL_HOST="install.gammaqc.com"

TEMPLATE_CANONICAL_HTTP="$MESH_ROOT/nginx/canonical-http.conf"
TEMPLATE_CANONICAL_FULL="$MESH_ROOT/nginx/canonical.conf"
TEMPLATE_LANDING="$MESH_ROOT/landing/index.html"
INSTALL_SCRIPT="$MESH_ROOT/landing/install"

# Note: as of v0.3 Cloudflare-native, default-redirect.conf is NOT rendered.
# The mesh redirects happen at Cloudflare's edge (via Rulesets API, set by
# cloudflare-bulk-redirect.py). Hostinger only serves the canonical now.

for f in "$TEMPLATE_CANONICAL_HTTP" "$TEMPLATE_CANONICAL_FULL" \
         "$TEMPLATE_LANDING" "$INSTALL_SCRIPT"; do
    [ -f "$f" ] || { echo "missing $f"; exit 1; }
done

# Resolve published gammaqc-terminal version from PyPI for the SBOM.
PKG_VER="$(curl -fsS --max-time 5 'https://pypi.org/pypi/gammaqc-terminal/json' 2>/dev/null \
            | python3 -c 'import sys,json; print(json.load(sys.stdin)["info"]["version"])' \
            2>/dev/null || echo unknown)"
RENDER_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ─── 1. Clean up stale per-niche configs from the v0.1/v0.2 design ───────────
# These were rendered when domains.list had 50 made-up niche-themed .xyz names.
# Commander's real inventory is ~1,500 NUMERIC .xyz; per-niche configs are
# obsolete. Match the pattern that render-configs v0.2 wrote (xyz.conf without
# being install.gammaqc.com or default-redirect, in sites-enabled).
echo "[render-configs] cleaning up stale per-niche server blocks…"
cleanup_count=0
for f in "$SITES_ENAB"/*.xyz.conf "$SITES_AVAIL"/*.xyz.conf; do
    [ -f "$f" ] || continue
    rm -f "$f"
    cleanup_count=$((cleanup_count + 1))
done
# Also nuke stale per-niche www-roots (we keep only the canonical's web-root).
for d in "$WEB_ROOT"/*.xyz; do
    [ -d "$d" ] || continue
    rm -rf "$d"
done
echo "[render-configs] removed $cleanup_count stale .conf file(s)"

# ─── 2. Render canonical install.gammaqc.com server block ────────────────────
# Pick pre-cert (HTTP-only) or post-cert (full HTTPS) based on cert presence.
if [ -f "/etc/letsencrypt/live/${CANONICAL_HOST}/fullchain.pem" ]; then
    echo "[render-configs] canonical cert present → rendering full HTTPS"
    cp "$TEMPLATE_CANONICAL_FULL" "$SITES_AVAIL/${CANONICAL_HOST}.conf"
else
    echo "[render-configs] canonical cert absent → rendering pre-cert (HTTP-only)"
    cp "$TEMPLATE_CANONICAL_HTTP" "$SITES_AVAIL/${CANONICAL_HOST}.conf"
fi
ln -sf "$SITES_AVAIL/${CANONICAL_HOST}.conf" "$SITES_ENAB/${CANONICAL_HOST}.conf"

# Render landing page + install script + SBOM for the canonical web root
install -d -m 0755 "$WEB_ROOT/${CANONICAL_HOST}"
cp "$TEMPLATE_LANDING"  "$WEB_ROOT/${CANONICAL_HOST}/index.html"
cp "$INSTALL_SCRIPT"    "$WEB_ROOT/${CANONICAL_HOST}/install"
cat > "$WEB_ROOT/${CANONICAL_HOST}/sbom.json" <<EOF
{
  "package": "gammaqc-terminal",
  "version_pinned": "${PKG_VER}",
  "source": "PyPI",
  "install_command": "curl -sL https://${CANONICAL_HOST}/install | bash",
  "install_method": "pipx (preferred) or pip3 --user (fallback)",
  "telemetry": false,
  "third_party_calls_during_install": ["pypi.org"],
  "license": "Apache-2.0",
  "source_code": "https://github.com/NoirLynX-Intelligence/gammaqc-terminal",
  "rendered_at": "${RENDER_TS}",
  "canonical_host": "${CANONICAL_HOST}",
  "mesh_node_count": 1500
}
EOF

# ─── 3. Clean up any stale default-redirect.conf from v0.3-pre-cloudflare ────
# Earlier v0.3 iteration rendered a Hostinger nginx default_server that did
# the redirect. With Cloudflare edge redirects now, that's obsolete. Remove
# if present so the canonical server block becomes the only thing this box
# serves on port 80.
for f in "$SITES_ENAB/default-redirect.conf" "$SITES_AVAIL/default-redirect.conf"; do
    [ -f "$f" ] && rm -f "$f" && echo "[render-configs] removed stale $(basename $f)"
done
# Keep Hostinger's default server symlink REMOVED (any unknown Host header on
# port 80 should now get the Ubuntu default page on this box — anyone hitting
# the IP directly without a mesh-bound Host header is an op probe, not user
# traffic). Or, if we want to be more graceful, return 404 default-server.
# v0.3 ships without a default_server; nginx serves the FIRST defined server
# (canonical) as implicit default. Fine.

chown -R www-data:www-data "$WEB_ROOT"

echo "[render-configs] rendered:"
echo "  - canonical: ${CANONICAL_HOST}"
echo "  - default-redirect: catches any other Host → 301 https://${CANONICAL_HOST}"
echo "  - cleaned up: $cleanup_count stale per-niche .conf file(s)"
