// gammaqc-mesh-redirect — Cloudflare Worker.
//
// Bound to all 923 GammaQC mesh zones (1333xxx.xyz). Receives every request
// hitting any of those zones and 301-redirects to install.gammaqc.com,
// preserving the original path + query string.
//
// Replaces the older broken Worker(s) currently bound to these zones that
// return "Cannot read properties of undefined (reading 'get')" 500 errors.
//
// Service Worker syntax (not module) so the deploy script can PUT the
// raw text via Cloudflare API without needing wrangler or a build step.
//
// Honesty contract:
//   - Pure passthrough redirect. No tracking, no headers added, no logging
//     beyond Cloudflare's standard edge logs.
//   - Cache-Control: no-store on the 301 itself so we can ship hotfixes
//     (e.g. change canonical) without CDN propagation lag.
//   - The 301 status code lets curl + bash + browsers all follow it
//     automatically. canonical link signal for SEO consolidation.

const CANONICAL = 'https://install.gammaqc.com';

addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const target = CANONICAL + url.pathname + url.search;
  event.respondWith(
    new Response(null, {
      status: 301,
      headers: {
        'location': target,
        'cache-control': 'no-store, no-cache, must-revalidate, max-age=0',
        'x-gammaqc-mesh': 'worker-redirect',
      },
    }),
  );
});
