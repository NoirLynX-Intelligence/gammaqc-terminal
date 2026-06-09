// gammaqc-mesh-redirect — Cloudflare Worker.
//
// v0.2 (2026-06-09 evening): adds privacy-respecting install attribution
// logging to a CF KV namespace (binding: ANALYTICS). Each request is
// logged with:
//   - source mesh hostname (the .xyz the user hit)
//   - request path (/install / /sbom / /healthz / other)
//   - client IP bucketed to /24 (last octet stripped — never full IP)
//   - cf_country + cf_colo (CF edge geo data, already in request)
//   - unix timestamp
// NO user-agent, NO referer, NO cookies, NO query string, NO full IP.
// Privacy-respecting attribution; tells us which mesh nodes converted
// without identifying any individual user. KV write happens via
// ctx.waitUntil so it never adds latency to the user's redirect.
//
// Bound to all 923 GammaQC mesh zones (1333xxx.xyz). Receives every
// request, logs the event, then 301-redirects to install.gammaqc.com
// preserving the original path + query string.
//
// Service Worker syntax (raw text via API, no build step). KV binding
// is optional — Worker degrades gracefully without it (logs to console
// only via the runtime).

const CANONICAL = 'https://install.gammaqc.com';
const LOG_TTL_S = 30 * 24 * 60 * 60;   // 30 days

function bucketIp(ip) {
  // /24 for IPv4 (strip last octet); /48 prefix for IPv6 (first 3 hex groups).
  // We never log the full IP — only the network bucket suitable for
  // "how many distinct networks installed today" aggregation.
  if (!ip) return 'unknown';
  if (ip.includes('.')) {
    const parts = ip.split('.');
    if (parts.length === 4) return `${parts[0]}.${parts[1]}.${parts[2]}.0`;
  }
  if (ip.includes(':')) {
    const parts = ip.split(':');
    if (parts.length >= 3) return `${parts[0]}:${parts[1]}:${parts[2]}::`;
  }
  return 'unknown';
}

async function logEvent(env, event) {
  // Best-effort. Never fail the redirect because analytics is down.
  if (!env || !env.ANALYTICS) return;
  try {
    // Key shape: install:<host>:<unix_ms>-<random4>
    // Lets us list-by-prefix per host + sort by time. Random suffix
    // dedupes if two requests land in the same millisecond.
    const ts = Date.now();
    const rand = Math.floor(Math.random() * 65536).toString(16).padStart(4, '0');
    const key = `install:${event.host}:${ts}-${rand}`;
    await env.ANALYTICS.put(key, JSON.stringify(event), {
      expirationTtl: LOG_TTL_S,
    });
  } catch (e) {
    // Console only — never re-throw.
    console.warn(`[analytics] write failed (non-fatal): ${e.message || e}`);
  }
}

addEventListener('fetch', (event) => {
  event.respondWith(handle(event.request, globalThis.__env || event.env, event));
});

export default {
  async fetch(request, env, ctx) {
    return handle(request, env, ctx);
  },
};

async function handle(request, env, ctx) {
  const url = new URL(request.url);
  const target = CANONICAL + url.pathname + url.search;

  // Build attribution event. Strips all PII; bucket-only IP.
  const event = {
    host: url.hostname,
    path: url.pathname,
    method: request.method,
    ip_bucket: bucketIp(request.headers.get('cf-connecting-ip') || ''),
    cf_country: (request.cf && request.cf.country) || 'unknown',
    cf_colo: (request.cf && request.cf.colo) || 'unknown',
    ts_unix: Math.floor(Date.now() / 1000),
  };

  // Fire analytics in background — DO NOT await before responding.
  // ctx.waitUntil ensures the write completes after the response is sent
  // to the user (zero added latency on the redirect path).
  if (ctx && ctx.waitUntil) {
    ctx.waitUntil(logEvent(env, event));
  } else {
    // Fallback for runtime without ctx — fire and forget
    logEvent(env, event);
  }

  return new Response(null, {
    status: 301,
    headers: {
      'location': target,
      'cache-control': 'no-store, no-cache, must-revalidate, max-age=0',
      'x-gammaqc-mesh': 'worker-redirect',
    },
  });
}
