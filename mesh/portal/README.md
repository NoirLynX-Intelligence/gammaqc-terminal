# gammaqc-portal — brand-correct www.gammaqc.com landing

Self-contained CF Worker serving the Gamma QC landing page. Replaces
the prior "Resonance"-headlined Pages deploy per Commander stop-drift
directive: **Gamma QC is the brand; Resonance is a downstream outcome.**

## What changed vs the old landing

| Element | Old | New |
|---|---|---|
| `<title>` | Resonance — DrkLynX Gamma QC | Gamma QC — DrkLynX Sovereign Quantitative Finance |
| `<h1>` | Resonance | Gamma QC |
| Eyebrow | Module-Γ · Financial Intelligence | Module-Γ · Quantitative Cryptography · Quality Control |
| Tagline | (none) | The DrkLynX Sovereign Finance Module |
| Body | (Resonance was the headline) | "The *resonance* you feel when conviction meets the regime — that is the outcome. The platform is Gamma QC." |
| OG / PWA name | DrkLynX Portal | Gamma QC |
| Module table | +CTA row | Adds `pip install gammaqc-terminal` row |

Aesthetic (Swiss Brutalist gold-on-charcoal, Γ symbol, pulsing dot,
PWA install chip, scrub indicator) is **unchanged**. Only brand
hierarchy was corrected.

## Deploy

```bash
cd mesh/portal
python3 build.py                          # inlines index.html into worker.js
wrangler deploy                           # ships to workers.dev for A/B
```

Verify on the workers.dev URL printed by wrangler:
- Visit root — `<h1>` must say Gamma QC
- Visit `/.gamma/diag` — JSON must include `"h1": "Gamma QC"`

Once green, in CF dashboard:
1. Workers & Pages → gammaqc-portal → Settings → Triggers
2. Add Route: `gammaqc.com/*` zone `gammaqc.com`
3. Add Route: `www.gammaqc.com/*` zone `gammaqc.com`

Routes take precedence over the old Pages deploy. Hard-refresh the
PWA on any device to flush the cached HTML.

## Architecture notes

- **Single-file Worker** — landing HTML inlined as a JS template literal
  via `build.py`. ~12KB; CF Worker free tier supports up to 1MB. No
  R2/KV/D1/DO dependencies; deploys atomically.
- **Defense-in-depth headers** — CSP, HSTS preload, X-Content-Type-Options,
  Permissions-Policy. Beats the prior deploy on security headers.
- **Fall-through is 404** — for paths other than `/`, `/healthz`,
  `/manifest.webmanifest`, `/.gamma/diag`. Until we migrate the icon/
  manifest assets, the original Pages deploy continues to serve
  `/icons/*` and `/sw.js`. Route precedence on `/` means the new H1
  shows immediately; static assets remain on the old origin until full
  migration.
- **Brand check endpoint** — `/.gamma/diag` returns a JSON probe that
  asserts `h1: "Gamma QC"`. Useful for an automated brand-regression
  alarm (e.g. `gamma verify` could optionally hit it).

## Files

```
mesh/portal/
├── README.md       — this file
├── index.html      — source HTML (edit this for brand/copy changes)
├── worker.js       — Worker scaffold with INLINE_HTML placeholder
├── build.py        — inlines index.html into worker.js → dist/worker.js
├── wrangler.toml   — CF Worker config
└── dist/
    └── worker.js   — built artifact (gitignored — rebuild before deploy)
```

## Why a new Worker instead of editing the old Pages deploy

The original page source lives only in an old session transcript, not
in any active local repo. Standing up a fresh, locally-versioned Worker:

- Fast (no Pages archaeology)
- De-risks parallel A/B verification (old + new running side-by-side
  on different routes until we cut over)
- Source-of-truth lives in this repo now, so future brand corrections
  are a one-line edit + redeploy
- Better security headers than the prior deploy
