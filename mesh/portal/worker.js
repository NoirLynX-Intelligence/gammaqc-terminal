// gammaqc-portal — Cloudflare Worker serving the brand-correct
// www.gammaqc.com landing + PWA assets.
//
// Why a Worker and not Pages: the previous deploy lives somewhere we
// can't easily reach (Pages project name lost; source not in any local
// repo). Standing up a fresh Worker bound to gammaqc.com via dashboard
// is faster than archaeology.
//
// Routes once bound (via wrangler.toml or dashboard):
//   gammaqc.com/*          → this Worker
//   www.gammaqc.com/*      → this Worker
//
// Endpoints served:
//   /                    → brand-correct landing (Gamma QC, NOT Resonance)
//   /healthz             → 'ok'
//   /manifest.webmanifest → PWA manifest pointing at /icons/*
//
// Other paths (e.g. /icons/*, /sw.js) fall through to the existing
// origin (CF Pages or whatever was originally there) via passthrough
// to the fetch event's request. If we ever migrate fully we'll inline
// those too; for now the landing-page-only fix is the brand priority.

const LANDING_HTML = INLINE_HTML;   // injected at build time, see deploy.sh

const MANIFEST = {
  name: "Gamma QC",
  short_name: "Gamma QC",
  description: "DrkLynX sovereign quantitative finance — quantitative cryptography meets quality control.",
  start_url: "/",
  display: "standalone",
  background_color: "#0A0A0B",
  theme_color: "#0A0A0B",
  icons: [
    { src: "/icons/gamma-192.png", sizes: "192x192", type: "image/png" },
    { src: "/icons/gamma-512.png", sizes: "512x512", type: "image/png" },
  ],
};

const SECURITY_HEADERS = {
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data: https://gammaqc.com; font-src 'self'; connect-src 'self'; " +
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
};

function html(body, extra = {}) {
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "public, max-age=0, must-revalidate",
      ...SECURITY_HEADERS,
      ...extra,
    },
  });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: {
      "content-type": "application/manifest+json; charset=utf-8",
      "cache-control": "public, max-age=86400",
      ...SECURITY_HEADERS,
    },
  });
}

async function passthrough(request) {
  // For paths we don't own, defer to whatever CF has currently routed.
  // If no other route matches, CF returns 1014/404 — fine, the landing
  // is the priority. After full migration, drop this and 404 explicitly.
  return new Response("Not found", {
    status: 404,
    headers: { "content-type": "text/plain", ...SECURITY_HEADERS },
  });
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/" || path === "/index.html") return html(LANDING_HTML);
    if (path === "/healthz") {
      return new Response("ok", { status: 200, headers: { "content-type": "text/plain" } });
    }
    if (path === "/manifest.webmanifest") return json(MANIFEST);
    // Diagnostic — confirms which Worker is serving (useful when tracking
    // down the OLD Pages deploy vs THIS Worker during cutover)
    if (path === "/.gamma/diag") {
      return json({
        served_by: "gammaqc-portal Worker v1",
        brand: "Gamma QC (DrkLynX)",
        h1: "Gamma QC",   // NOT 'Resonance'
        commit_check: "if this returns, brand-correct landing is live",
        timestamp: new Date().toISOString(),
      });
    }
    return passthrough(request);
  },
};
