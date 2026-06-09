// install-analytics-summary — read-side Worker for the install
// attribution log written by gammaqc-mesh-redirect.
//
// Companion to mesh/workers/redirect.js. Exposes a single endpoint:
//
//   GET /summary?since_hours=24
//   Authorization: Bearer <ANALYTICS_READ_TOKEN>
//
// Returns aggregated install events for the past N hours:
//   - total install requests
//   - unique source mesh hosts that received traffic
//   - unique IP buckets (/24 v4 or /48 v6) that hit /install
//   - top 20 mesh nodes by request count (which posts converted)
//   - country distribution (already in cf.country, no extra lookup)
//   - per-path breakdown (/install vs /sbom vs /healthz vs other)
//
// Auth: simple bearer token (ANALYTICS_READ_TOKEN secret). Not user-
// facing — only Commander / ops checks this. Wrong token → 401.
//
// Bound to a workers.dev URL or a private subdomain. NOT routed
// through any mesh node (this is the read side, doesn't touch
// redirect traffic at all).

const SCRIPT_VERSION = 'install-analytics-summary/v0.1';

function asJson(status, body) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

async function listKeys(env, prefix, limit) {
  // KV list returns up to 1000 keys per call. For higher volumes,
  // paginate via cursor. For the launch (low daily volume initially),
  // a single list call is enough.
  const out = [];
  let cursor = undefined;
  while (out.length < limit) {
    const page = await env.ANALYTICS.list({
      prefix,
      limit: Math.min(1000, limit - out.length),
      cursor,
    });
    out.push(...page.keys);
    if (page.list_complete || !page.cursor) break;
    cursor = page.cursor;
  }
  return out;
}

async function buildSummary(env, sinceUnix) {
  const keys = await listKeys(env, 'install:', 10000);

  const stats = {
    total_requests: 0,
    unique_hosts: new Set(),
    unique_ip_buckets: new Set(),
    by_host: {},
    by_path: {},
    by_country: {},
    by_colo: {},
  };

  // Read all values in parallel (KV reads are fast at edge)
  const promises = keys.map(async (k) => {
    const raw = await env.ANALYTICS.get(k.name);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });
  const events = (await Promise.all(promises)).filter(Boolean);

  for (const ev of events) {
    if (ev.ts_unix < sinceUnix) continue;
    stats.total_requests += 1;
    stats.unique_hosts.add(ev.host);
    if (ev.path === '/install') {
      stats.unique_ip_buckets.add(ev.ip_bucket);
    }
    stats.by_host[ev.host] = (stats.by_host[ev.host] || 0) + 1;
    stats.by_path[ev.path] = (stats.by_path[ev.path] || 0) + 1;
    stats.by_country[ev.cf_country] = (stats.by_country[ev.cf_country] || 0) + 1;
    stats.by_colo[ev.cf_colo] = (stats.by_colo[ev.cf_colo] || 0) + 1;
  }

  // Sort by_host descending, take top 20
  const top_hosts = Object.entries(stats.by_host)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 20)
    .map(([host, count]) => ({ host, count }));

  return {
    script_version: SCRIPT_VERSION,
    since_unix: sinceUnix,
    since_iso: new Date(sinceUnix * 1000).toISOString(),
    generated_at_iso: new Date().toISOString(),
    total_requests: stats.total_requests,
    unique_hosts_count: stats.unique_hosts.size,
    unique_ip_buckets_count_on_install_path: stats.unique_ip_buckets.size,
    top_20_hosts: top_hosts,
    by_path: stats.by_path,
    by_country: stats.by_country,
    by_colo: stats.by_colo,
  };
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== 'GET') {
      return asJson(405, { error: 'method not allowed' });
    }
    const url = new URL(request.url);
    if (url.pathname !== '/summary') {
      return asJson(404, { error: 'not found' });
    }

    // Auth
    const auth = request.headers.get('authorization') || '';
    const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
    if (!env.ANALYTICS_READ_TOKEN || token !== env.ANALYTICS_READ_TOKEN) {
      return asJson(401, { error: 'unauthorized' });
    }
    if (!env.ANALYTICS) {
      return asJson(500, { error: 'ANALYTICS KV binding missing' });
    }

    const sinceHours = parseInt(url.searchParams.get('since_hours') || '24', 10);
    const cappedHours = Math.max(1, Math.min(720, sinceHours));   // 1h to 30d
    const sinceUnix = Math.floor(Date.now() / 1000) - cappedHours * 3600;

    try {
      const summary = await buildSummary(env, sinceUnix);
      return asJson(200, summary);
    } catch (e) {
      return asJson(500, { error: `summary failed: ${e.message || e}` });
    }
  },
};
