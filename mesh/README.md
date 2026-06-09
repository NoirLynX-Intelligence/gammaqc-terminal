# gammaqc-terminal — 500-Domain Install Mesh

> Hostinger KVM2 + Nginx + Let's Encrypt. One install script, one landing
> page template, served from any of 500 .xyz sovereign mesh domains.

```
   sec-earnings-cli.xyz       ┐
   crypto-options-tape.xyz    │      Hostinger KVM2 (single box)
   texas-energy-quant.xyz     │      ┌────────────────────────────┐
   swiss-compliance-cli.xyz   ├────► │  Nginx (one config)        │
   ...                        │      │   ├── /         → landing  │
   (500 hyper-niche domains)  │      │   ├── /install  → bash     │
                              ┘      │   └── /sbom     → manifest │
                                     └────────────────────────────┘
                                             │
                                             ▼
                                   pip install gammaqc-terminal
                                   (from PyPI)
```

## What this is

A faceless distribution fabric. The CLI itself ships via PyPI; the 500
.xyz domains are SEO honeypots that funnel hyper-niche queries to a
single curl-pipe-bash command. Each domain has its own landing page
keyword-tuned to the niche (energy quants, crypto compliance, REIT
investors, etc.) but ALL of them install the exact same package.

This file documents the layout. Run `./scripts/bootstrap.sh` on the
Hostinger box to install Nginx + Certbot + lay down the configs.

## Privacy contracts

- The install endpoint serves a static script. **Zero telemetry on the
  install path.** No tracking cookies, no analytics pixel, no log of
  which domain the user came from.
- The landing pages are static HTML. No JS, no third-party fonts, no
  external embeds. Swiss-Brutalist minimalism — the page IS the proof
  of seriousness.
- Nginx access logs are rotated daily and retained 7 days for ops
  debugging only. No personally-identifying fields are logged.
- Let's Encrypt certificates renew via certbot's standard cron; we do
  NOT use Cloudflare or any third-party CDN in front of these domains
  (the CDN would see install traffic — defeats the sovereign promise).

## Layout

```
mesh/
├── README.md                  ← this file
├── nginx/
│   ├── gammaqc-mesh.conf      ← single Nginx server block (templated)
│   ├── nginx.conf.snippet     ← include-path for the main nginx.conf
│   └── domains.list           ← the 500 domains, one per line
├── landing/
│   ├── index.html             ← Swiss-Brutalist landing template
│   └── install                ← the bash script users curl-pipe
└── scripts/
    ├── bootstrap.sh           ← initial Hostinger box setup (idempotent)
    ├── render-configs.sh      ← expands the template → 500 server blocks
    ├── certbot-bulk.sh        ← obtains certs in batches (LE rate limits)
    └── verify-mesh.sh         ← smoke-tests every domain returns 200
```

## Bootstrap sequence (Commander runs ONCE on Hostinger KVM2)

```bash
# As root on the KVM2 box (Ubuntu 22.04+ assumed):

# 1. Sync this mesh/ dir to the box
rsync -avz mesh/ root@<hostinger-ip>:/opt/gammaqc-mesh/

# 2. Bootstrap
ssh root@<hostinger-ip> 'cd /opt/gammaqc-mesh && bash scripts/bootstrap.sh'

# 3. Point ALL 500 domains' A records at the Hostinger IP
#    (Hostinger DNS panel; bulk import supported via CSV)

# 4. Generate certs in batches (Let's Encrypt rate-limits ~50/wk per IP)
ssh root@<hostinger-ip> 'cd /opt/gammaqc-mesh && bash scripts/certbot-bulk.sh'

# 5. Smoke-test
ssh root@<hostinger-ip> 'cd /opt/gammaqc-mesh && bash scripts/verify-mesh.sh'
```

After that, every domain serves `/install` returning the same bash
script, and `/` returning a niche-tuned landing page.

## Per-domain landing customization

`landing/index.html` is a template with two placeholders:

- `{{DOMAIN}}` — full hostname (e.g. `sec-earnings-cli.xyz`)
- `{{NICHE_HOOK}}` — niche-specific subhead (resolved from
  `nginx/domains.list` second column)

Example `domains.list` row format:
```
sec-earnings-cli.xyz   For SEC-filing quants who want their earnings drops in <1 second.
texas-energy-quant.xyz For Texas energy traders who need Bloomberg-grade vol on a laptop.
```

The render script substitutes these per-domain at deploy time, so each
domain has organically-different content for SEO without manual work.

## Why not Cloudflare in front?

A CDN sees every install request. The sovereign promise of this tool
is "your data NEVER leaves your machine in free mode." Putting CF in
front would mean Cloudflare sees:
- Your IP
- Which niche domain referred you
- A fingerprint of your install events

Origin-only with LE certs preserves the honest contract: only Hostinger
+ you see your install request, and even those logs rotate every 7 days.

When traffic warrants, the next step is **anycast on sovereign rails**
(XRPL/Carterra) — NOT a third-party CDN.
