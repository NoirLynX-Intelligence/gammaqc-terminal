# install-analytics — per-mesh-node attribution for Gamma QC launch

> Read-side companion to `mesh/workers/redirect.js` v0.2. The redirect
> Worker WRITES install events to KV (privacy-respecting; bucket-only
> IPs, no UA/Referer); this Worker READS + aggregates for ops queries.

## What it tracks (privacy-respecting)

Per request to any mesh node:
- Source mesh hostname (e.g. `1333077.xyz`) — which post converted
- Request path (`/install` / `/sbom` / `/healthz` / other)
- HTTP method
- Client IP bucketed to **/24** (IPv4) or **/48** (IPv6) — **never the full IP**
- CF country code (already in request, no extra lookup)
- CF edge POP (`cf.colo`) — for geo distribution analysis
- Unix timestamp

**What it does NOT log:** user-agent, referer, cookies, query string,
full IP, session tokens. Aggregation by /24 lets us answer "how many
distinct networks installed today" without identifying anyone.

## Setup (one-time, before Wave 1 launch)

### 1. Create the KV namespace

From the gammaqc-terminal repo root:

```bash
cd mesh/workers/install-analytics
wrangler kv:namespace create install_analytics
# Output: id = "abc123def456..."
wrangler kv:namespace create install_analytics --preview
# Output: preview_id = "xyz789..."
```

Paste both IDs into `wrangler.toml` under `[[kv_namespaces]]`.

### 2. Bind the same KV namespace to the redirect Worker

Edit `mesh/workers/email-monster-smtp/wrangler.toml` (or wherever the
`gammaqc-mesh-redirect` Worker config lives) to add:

```toml
[[kv_namespaces]]
binding = "ANALYTICS"
id = "<same id from step 1>"
preview_id = "<same preview_id from step 1>"
```

Re-deploy the redirect Worker so the binding takes effect:

```bash
wrangler deploy   # from the redirect Worker's project dir
```

### 3. Generate an ops-only read token + set it

```bash
TOKEN=$(openssl rand -hex 32)
echo "$TOKEN" > ~/.gammaqc-analytics-read-token.txt   # keep this!
echo -n "$TOKEN" | wrangler secret put ANALYTICS_READ_TOKEN
```

### 4. Deploy the summary Worker

```bash
wrangler deploy
# URL: install-analytics-summary.<your-subdomain>.workers.dev
```

## Query usage

```bash
# Last 24 hours (default)
TOKEN=$(cat ~/.gammaqc-analytics-read-token.txt)
curl -H "Authorization: Bearer $TOKEN" \
  https://install-analytics-summary.<your-subdomain>.workers.dev/summary

# Last 6 hours (during a Wave 1 post going viral)
curl -H "Authorization: Bearer $TOKEN" \
  "https://install-analytics-summary.<your-subdomain>.workers.dev/summary?since_hours=6"

# Last 7 days (week-over-week)
curl -H "Authorization: Bearer $TOKEN" \
  "https://install-analytics-summary.<your-subdomain>.workers.dev/summary?since_hours=168"
```

## Response shape

```json
{
  "script_version": "install-analytics-summary/v0.1",
  "since_unix": 1717891200,
  "since_iso": "2026-06-08T19:00:00.000Z",
  "generated_at_iso": "2026-06-09T19:00:00.000Z",
  "total_requests": 2847,
  "unique_hosts_count": 412,
  "unique_ip_buckets_count_on_install_path": 638,
  "top_20_hosts": [
    {"host": "1333077.xyz", "count": 184},
    {"host": "1333500.xyz", "count": 92},
    ...
  ],
  "by_path": {"/install": 1843, "/": 612, "/sbom": 298, "/healthz": 94},
  "by_country": {"US": 1421, "GB": 318, "DE": 287, ...},
  "by_colo": {"IAD": 612, "LHR": 287, "FRA": 198, ...}
}
```

## What the numbers mean for Wave 1 decisions

- **`top_20_hosts`** — which posts converted. If `1333077.xyz` is at
  the top after a r/algotrading drop, you know that post worked.
- **`unique_ip_buckets_count_on_install_path`** — rough lower bound
  on distinct people who installed (one /24 might serve a small office
  or a large carrier-grade NAT — bucket count is a lower bound for
  large companies, upper bound for residential). Pair with PyPI
  download counts for triangulation.
- **`by_country`** — does the Reddit drop pull mostly US, or did HN
  pull EU + APAC overnight? Tells you which surface to push next.
- **`by_path`** — `/install` is the actual install event; `/` is the
  landing page (could be just a visitor); `/sbom` is the privacy-
  conscious user verifying before installing. Ratio of `/sbom` to
  `/install` = how many people read before piping.

## What this Worker does NOT do

- No real-time alerts (no Slack webhook, no email)
- No persistent storage beyond 30d KV TTL
- No PII storage
- No cross-session linkage (no user IDs, no fingerprints)

If you want real-time install-event Slack pings (e.g. "10 new installs
in the past hour"), that's a separate Worker that consumes the same
KV namespace via scheduled cron. Not in scope for tonight's launch.
