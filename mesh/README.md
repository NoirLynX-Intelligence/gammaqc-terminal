# gammaqc-terminal — install mesh (v0.3)

> Hostinger KVM2 + Nginx + Let's Encrypt. **One canonical install URL +
> ~1,500-node redirect fabric** powered by the DrkLynX numeric .xyz mesh.

## Architecture

```
       USER hits any of:
         curl -sL https://install.gammaqc.com/install | bash
         curl -sL http://1334077.xyz/install | bash         (301 → canonical)
         curl -sL http://1411099.xyz/install | bash         (301 → canonical)
         …
                       │
                       ▼
       ┌─────────────────────────────────────┐
       │ Hostinger KVM2 (187.124.95.35)      │
       │ ┌─────────────────────────────────┐ │
       │ │ install.gammaqc.com (HTTPS)     │ │   ← single LE cert, full TLS
       │ │  └─ landing / install / sbom    │ │
       │ │                                 │ │
       │ │ default-redirect (HTTP catch)   │ │   ← 301 → canonical, preserves $request_uri
       │ └─────────────────────────────────┘ │
       └─────────────────────────────────────┘
                       │
                       ▼
              pip install gammaqc-terminal    ← already live on PyPI
```

## Domain inventory allocation (Commander 2026-06-09)

The full ~1,500-domain numeric .xyz inventory is shared across projects:

| Allocation | Pattern | Count | Use |
|---|---|---|---|
| **GammaQC** | `^1333\d{3}\.xyz$` (capped at 1000) | 923 | This install fabric — redirect mesh to install.gammaqc.com |
| **DrkLynX** | `1334xxx.xyz` + `1411xxx.xyz` + named | 603 | Sovereign edge mesh — separate use; not touched by this repo |

The partition is **hard-coded** in `scripts/extract-gammaqc-domains.py` so the
bulk-DNS script can never accidentally claim a DrkLynX domain — only the 923
allocated to GammaQC ever get A-records flipped to point at this box.

To regenerate the allocation file (e.g. when you import a new NameCheap CSV):
```bash
python3 mesh/scripts/extract-gammaqc-domains.py \
    --input <new-namecheap-export>.csv \
    --output mesh/data/domains-gammaqc.csv
```

## What's in this directory

```
mesh/
├── README.md                          ← this file
├── nginx/
│   ├── canonical-http.conf            ← pre-cert HTTP-only template for install.gammaqc.com
│   ├── canonical.conf                 ← post-cert full HTTPS template for install.gammaqc.com
│   ├── default-redirect.conf          ← catch-all 301 → canonical (the mesh fabric)
│   └── nginx.conf.snippet             ← privacy log format (lean: just log_format)
├── landing/
│   ├── index.html                     ← universal landing (no per-domain templating)
│   └── install                        ← byte-identical curl-pipe-bash script
└── scripts/
    ├── bootstrap.sh                   ← idempotent Hostinger box setup
    ├── render-configs.sh              ← render canonical + default-redirect, clean stale configs
    ├── certbot-canonical.sh           ← issue ONE LE cert for install.gammaqc.com
    ├── verify-mesh.sh                 ← smoke-test canonical + sample mesh nodes
    └── namecheap-bulk-dns.py          ← bulk-set A-records for ~1,500 NC domains
```

## Bootstrap sequence

**Phase A — canonical install endpoint (~10 min)**

```bash
# 1. Sync mesh/ to the box (from your dev machine or the existing /tmp clone)
ssh root@187.124.95.35 "cd /tmp/gammaqc && git pull -q && cp -r mesh/. /opt/gammaqc-mesh/"

# 2. Render the new config + clean up stale per-niche server blocks from v0.1/v0.2
ssh root@187.124.95.35 "cp /opt/gammaqc-mesh/nginx/nginx.conf.snippet /etc/nginx/snippets/gammaqc-mesh.conf && bash /opt/gammaqc-mesh/scripts/render-configs.sh && nginx -t && systemctl reload nginx"

# 3. Add DNS A-record for install.gammaqc.com at wherever gammaqc.com DNS lives
#    install.gammaqc.com → 187.124.95.35
#    (do this in your DNS provider's panel)

# 4. Wait for DNS to propagate (~5 min), then issue the canonical LE cert
ssh root@187.124.95.35 "bash /opt/gammaqc-mesh/scripts/certbot-canonical.sh"

# 5. Smoke-test
ssh root@187.124.95.35 "bash /opt/gammaqc-mesh/scripts/verify-mesh.sh"
```

**Phase B — bulk DNS for the 923 GammaQC mesh nodes (~21 min once API key is ready)**

```bash
# Pre-flight (do once at https://ap.www.namecheap.com → Profile → Tools → API Access):
#   - Enable API access
#   - Whitelist 187.124.95.35 (the box's IP — script must run from there)
#   - Generate API key, copy it

# Then on the Hostinger box:
ssh root@187.124.95.35
export NC_API_USER=<your_nc_username>
export NC_API_KEY=<your_nc_api_key>
export NC_API_USERNAME=<your_nc_username>     # same as NC_API_USER for solo accounts
export NC_CLIENT_IP=187.124.95.35

# Test with --limit 5 first to validate auth + DNS shape on a small batch
# The pre-allocated GammaQC list lives at mesh/data/domains-gammaqc.csv
# (923 domains from the 1333xxx range — DrkLynX domains EXCLUDED by partition).
python3 /opt/gammaqc-mesh/scripts/namecheap-bulk-dns.py \
    --csv /opt/gammaqc-mesh/data/domains-gammaqc.csv \
    --ip 187.124.95.35 \
    --limit 5

# If green, run the full GammaQC allocation (~21 min at 45 req/min)
python3 /opt/gammaqc-mesh/scripts/namecheap-bulk-dns.py \
    --csv /opt/gammaqc-mesh/data/domains-gammaqc.csv \
    --ip 187.124.95.35

# Verify a sample of the mesh now redirects
bash /opt/gammaqc-mesh/scripts/verify-mesh.sh --samples 20
```

**Phase C — optional HTTPS rollout on mesh nodes (weeks-long, only if needed)**

Skip unless users complain about needing `https://1334077.xyz` directly. For
`curl -sL` with redirects, HTTP-only on the mesh is fine — curl follows the
301 to HTTPS canonical automatically. If/when needed, see Phase C in CHANGELOG.

## Privacy contracts

- The **canonical endpoint** terminates TLS directly on the Hostinger box.
  No CDN in front. No third-party in the install path.
- The **mesh redirect fabric** is HTTP-only by design. It logs request lines
  but NEVER user-agent, referer, or cookies (per `log_format mesh_privacy`),
  and strips the last IPv4 octet to /24 before logging. 7-day retention.
- The **install script** makes ONE network call: `pip install gammaqc-terminal`
  from PyPI. No telemetry. No phone-home. Verify before piping:
  `curl -sL https://install.gammaqc.com/install | less`

## Why HTTP on the mesh is OK

The install script contains no secrets. It's a 30-line bash script that:
1. Detects pipx or pip
2. Runs `pipx install gammaqc-terminal` (which fetches from PyPI over HTTPS)

The only HTTPS-required surface is the PyPI download itself, which is
handled by pip with its own cert validation. The mesh redirect happens
BEFORE any download — by the time bytes flow, we're already on the
canonical's HTTPS connection or on PyPI's.

This is the same pattern `get.docker.com` and `sh.rustup.rs` use for their
install vectors.
