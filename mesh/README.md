# gammaqc-terminal — install mesh (v0.3, Cloudflare-native)

> 923 GammaQC mesh nodes redirect at the Cloudflare edge → one canonical
> install endpoint on Hostinger KVM 2. Universal SSL on every mesh node
> (no per-domain LE certs needed).

## Architecture

```
   USER hits any of the 923 mesh nodes:
   curl -sL https://1333077.xyz/install
                  │
                  ▼
   ┌──────────────────────────────────────────┐
   │  CLOUDFLARE EDGE (anycast, free SSL)      │
   │   ├── Universal SSL on every domain       │
   │   └── Bulk Redirect Rule per zone:        │
   │       "any URL → https://install.         │
   │        gammaqc.com${path}${query}, 301"   │
   └──────────────────────────────────────────┘
                  │ 301 (handled at edge — never hits our box)
                  ▼
   ┌──────────────────────────────────────────┐
   │  Hostinger KVM 2 (187.124.95.35)          │
   │   install.gammaqc.com (single LE cert)    │
   │     ├── /install   (the bash script)      │
   │     ├── /sbom      (manifest)             │
   │     └── /          (landing page)         │
   └──────────────────────────────────────────┘
                  │ pip install
                  ▼
              pypi.org (gammaqc-terminal 0.2.0)
```

**Why this is better than the v0.2 design:**
- Mesh nodes already on Cloudflare → free Universal SSL on every domain (skipped 30 weeks of LE rate-limiting)
- Redirects happen at Cloudflare edge (microseconds) instead of round-tripping through Hostinger
- Hostinger box only serves the canonical — no per-mesh-domain load
- Mesh expansion is bounded by CF account zones, not LE cert quota

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

**Phase B — Cloudflare Worker redirect (REVISED 2026-06-09 evening)**

We attempted Page Rules first. Failed: each of the 923 zones already had
a (broken) Cloudflare Worker bound that returned `Cannot read properties
of undefined (reading 'get')` 500 errors, intercepting requests BEFORE
Page Rules could fire (CF request order: WAF → Workers → Page Rules →
Origin).

Pivot: deploy a NEW Worker (`mesh/workers/redirect.js`, 22 lines) that
does the 301 redirect, replace the broken Worker's routes with ours on
all 923 zones. Cleaner than Page Rules anyway — one script, atomic
across zones, no per-zone rule management.

```bash
# Pre-flight CF API token permissions (https://dash.cloudflare.com/profile/api-tokens):
#   "Custom token" with these scopes on ALL zones in the account:
#     - Account : Workers Scripts   : Edit    ← deploy the Worker once
#     - Zone    : Workers Routes    : Edit    ← bind it to each zone
#     - Zone    : Zone              : Read    ← look up zone IDs
#     - Zone    : Zone Settings     : Edit    ← always_use_https toggle (optional)
#   No IP whitelist needed — the token bearer is the auth.

# Then run from ANYWHERE (laptop, Hostinger box, CI — pure API calls):
export CF_API_TOKEN=<your_cloudflare_token>

# Step 1. Deploy the Worker script once + bind to 3 zones (smoke test)
python3 mesh/scripts/cloudflare-deploy-worker.py \
    --csv mesh/data/domains-gammaqc.csv \
    --limit 3

# Step 2. If green, bind to all 923 zones (~12 min at 4 req/sec)
#         --skip-upload because the script is unchanged from step 1
python3 mesh/scripts/cloudflare-deploy-worker.py \
    --csv mesh/data/domains-gammaqc.csv \
    --skip-upload

# Step 3. Verify a sample of the mesh redirects
bash mesh/scripts/verify-mesh.sh --samples 20
```

The deploy script is idempotent — re-running deletes our prior route
(matched by pattern OR by script name pointing at gammaqc-mesh-redirect)
then creates fresh. Other Worker routes on the zone are LEFT ALONE
(so you can add intentional non-redirect Workers later without conflict).

## Why Workers instead of Page Rules

The 923 mesh zones each have an existing (broken) Cloudflare Worker bound
that intercepts requests before Page Rules fire. We replace those Workers
with our own redirect Worker — cleaner architecturally than fighting
the Worker-then-Page-Rule ordering.

Worker advantages on this surface:
- One script, 923 zone bindings (vs. 923 individual Page Rules)
- Atomic update: change `CANONICAL` in `redirect.js`, re-deploy → all 923
  flip simultaneously
- 4-line redirect logic, fully auditable
- Free tier supports 100k Worker invocations/day — easily enough for
  install traffic + bot noise combined

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
