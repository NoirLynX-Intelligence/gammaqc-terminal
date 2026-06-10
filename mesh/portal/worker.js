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

const LANDING_HTML = INLINE_HTML;   // injected at build time, see build.py
const SKILL_MD = INLINE_SKILL;       // injected at build time, see build.py

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

// ─── Skill manifest — JSON, fetch-cheap, signed when attestor is wired ───
//
// Discovery shape (intentionally narrow — easy for any LLM agent to parse):
//   {
//     "version": "0.3.0",
//     "name": "gamma-qc",
//     "content_url": "https://gammaqc.com/skill/raw",
//     "content_sha256": "sha256:...",
//     "content_bytes": 6064,
//     "issued_at": "2026-...Z",
//     "attestor": null | { "alg": "ed25519", "kid": "srf-...",
//                          "signature": "..." }
//   }
//
// content_sha256 lets a client cache by hash (skip re-fetch when unchanged).
// When SRF_ATTESTOR_URL + SRF_INTERNAL_KEY are bound to this Worker we'll
// POST the manifest core to the attestor and embed the Ed25519 signature
// in the response. Until those bindings land, attestor is null + a
// `degraded` field explains why — same honest-degraded pattern as the
// backend srf_receipt service.
//
// Cache: 5 min — short enough to bump version quickly, long enough to
// avoid hammering the attestor on every agent poll.

async function sha256Hex(text) {
  const enc = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}

// Extract version + name from the SKILL.md YAML frontmatter. Defensive:
// if the frontmatter parse fails for any reason, fall back to known-good
// defaults so the manifest endpoint never 500s.
function parseSkillFrontmatter(md) {
  try {
    const fm = md.match(/^---\n([\s\S]*?)\n---/);
    if (!fm) return { name: "gamma-qc", version: "0.0.0" };
    const lines = fm[1].split("\n");
    const out = { name: "gamma-qc", version: "0.0.0" };
    for (const line of lines) {
      const m = line.match(/^(\w+):\s*(.+)$/);
      if (m) out[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
    }
    return out;
  } catch {
    return { name: "gamma-qc", version: "0.0.0" };
  }
}

async function tryAttestorSign(env, receiptCore) {
  // Best-effort — if attestor env not bound, return null with reason.
  // Mirror the backend srf_receipt.attach_audit_receipt degraded pattern.
  const url = (env && env.SRF_ATTESTOR_URL) || "";
  const key = (env && env.SRF_INTERNAL_KEY) || "";
  if (!url || !key) return { signed: null, reason: "attestor:unconfigured" };
  try {
    const r = await fetch(url.replace(/\/+$/, "") + "/sign", {
      method: "POST",
      headers: {
        "authorization": "Bearer " + key,
        "content-type": "application/json",
      },
      body: JSON.stringify(receiptCore),
      // Aggressive timeout — never block the manifest on a slow attestor
      signal: AbortSignal.timeout(2500),
    });
    if (!r.ok) return { signed: null, reason: "attestor:http_" + r.status };
    const data = await r.json();
    if (!data.signature) return { signed: null, reason: "attestor:no_sig" };
    return {
      signed: {
        alg: data.alg || "ed25519",
        kid: (data.receipt && data.receipt.attestor && data.receipt.attestor.key_id) || null,
        signature: data.signature,
        signed_at: (data.receipt && data.receipt.attestor && data.receipt.attestor.signed_at) || null,
      },
      reason: null,
    };
  } catch (e) {
    return { signed: null, reason: "attestor:exception_" + (e.name || "unknown") };
  }
}

async function handleSkillManifest(request) {
  const fm = parseSkillFrontmatter(SKILL_MD);
  const contentHash = await sha256Hex(SKILL_MD);
  const issuedAt = new Date().toISOString();

  // Core fields that get signed — order-independent on the signer side
  // (RFC8785 canonicalization happens at the attestor Worker).
  const core = {
    artifact_kind: "skill",
    name: fm.name || "gamma-qc",
    version: fm.version || "0.0.0",
    content_url: "https://gammaqc.com/skill/raw",
    content_sha256: "sha256:" + contentHash,
    content_bytes: new TextEncoder().encode(SKILL_MD).length,
    issued_at: issuedAt,
  };

  // env is injected via the Worker runtime — in modern Module Workers
  // it's the 2nd arg of fetch(). Service-Worker style uses globalThis.
  // Reach it defensively.
  const env = (globalThis.__env) || {};
  const { signed, reason } = await tryAttestorSign(env, core);

  const manifest = {
    ...core,
    attestor: signed,
    degraded: signed ? [] : [reason || "attestor:unknown"],
    verifier_hint: signed
      ? "Verify offline: re-compute sha256 over /skill/raw response body, " +
        "compare to content_sha256. Then fetch JWKS at " +
        "https://attest.gammaqc.com/.well-known/attestor.json and verify " +
        "the Ed25519 signature against canonical(core fields)."
      : "This manifest is unsigned — attestor was unavailable at issue " +
        "time. Content hash still pins integrity. Re-fetch later for a " +
        "signed copy.",
  };

  return new Response(JSON.stringify(manifest, null, 2), {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "public, max-age=300",
      ...SECURITY_HEADERS,
    },
  });
}

// Skill landing page — gives the user a clear download CTA + an explanation
// of what the file is and how to use it with any LLM agent.
function renderSkillPage() {
  const SIZE_BYTES = new TextEncoder().encode(SKILL_MD).length;
  const SIZE_KB = (SIZE_BYTES / 1024).toFixed(1);
  const body = `<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gamma QC Skill — drop-in for Claude / ChatGPT / any LLM agent</title>
<meta name="description" content="Free Gamma QC skill file. Drop into your LLM agent and it will know how to use Gamma QC analysis, hedging, and signed receipts.">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:ui-monospace,'SF Mono','Menlo','Consolas',monospace;background:#0A0A0B;color:#F5F5F0;line-height:1.55;padding:24px}
.wrap{max-width:780px;margin:48px auto}
.eyebrow{font-size:11px;letter-spacing:0.18em;text-transform:uppercase;color:#C9A227;margin-bottom:18px;font-weight:500}
.eyebrow a{color:rgba(245,245,240,0.45);text-decoration:none;border-bottom:1px solid rgba(245,245,240,0.18)}
.eyebrow a:hover{color:#C9A227;border-bottom-color:#C9A227}
h1{font-size:30px;font-weight:600;letter-spacing:-0.01em;color:#F5F5F0;margin-bottom:14px}
.sub{font-size:15px;color:rgba(245,245,240,0.7);margin-bottom:28px;max-width:600px}
.cta-row{display:flex;gap:12px;margin-bottom:32px;flex-wrap:wrap}
.btn{display:inline-flex;flex-direction:column;padding:14px 22px;border:1px solid #C9A227;border-radius:2px;text-decoration:none;background:#C9A227;color:#0A0A0B;transition:background .15s}
.btn:hover{background:#E0B82F}
.btn-outline{background:transparent;color:#C9A227}
.btn-outline:hover{background:rgba(201,162,39,0.12)}
.btn-title{font-size:14px;font-weight:600;letter-spacing:-0.005em}
.btn-sub{font-size:11px;letter-spacing:0.04em;margin-top:3px;opacity:0.75}
.howto{background:#14130F;border:1px solid rgba(201,162,39,0.18);border-radius:2px;padding:22px;margin-bottom:24px}
.howto h2{font-size:14px;letter-spacing:0.12em;text-transform:uppercase;color:#C9A227;font-weight:500;margin-bottom:14px}
.howto ol{padding-left:22px;font-size:13px;color:rgba(245,245,240,0.85)}
.howto li{margin-bottom:10px}
.howto code{background:#0A0A0B;border:1px solid rgba(201,162,39,0.18);padding:2px 7px;border-radius:2px;color:#C9A227;font-size:12px;user-select:all;-webkit-user-select:all}
.preview{background:#0A0A0B;border:1px solid rgba(201,162,39,0.12);border-radius:2px;padding:18px;font-size:11px;color:rgba(245,245,240,0.5);max-height:240px;overflow:auto;white-space:pre-wrap;word-wrap:break-word}
.foot{margin-top:24px;padding-top:18px;border-top:1px solid rgba(201,162,39,0.08);font-size:10px;color:rgba(245,245,240,0.4);letter-spacing:0.06em;text-transform:uppercase}
.foot a{color:rgba(201,162,39,0.7);text-decoration:none}
</style></head><body><main class="wrap">
<div class="eyebrow"><a href="/">← Gamma QC</a> · Skill file · ${SIZE_KB} KB · open / portable</div>
<h1>Use Gamma QC inside your LLM</h1>
<p class="sub">Drop this file into your Claude, ChatGPT, or any agent runtime. Your LLM will know when to invoke Gamma QC, which endpoints to call, what the personas (Kim, Warren, Chemist) mean, and how to verify the cryptographic receipt that ships with every output.</p>
<div class="cta-row">
  <a href="/skill/download" class="btn"><span class="btn-title">Download SKILL.md</span><span class="btn-sub">${SIZE_KB} KB · save to disk</span></a>
  <a href="/skill/raw" class="btn btn-outline"><span class="btn-title">View raw</span><span class="btn-sub">paste as system prompt</span></a>
</div>
<div class="howto">
<h2>How to use</h2>
<ol>
<li><b>Claude Desktop / Claude Code:</b> save to <code>~/.claude/skills/gamma-qc/SKILL.md</code>. Claude auto-loads it when you ask anything market-shaped.</li>
<li><b>ChatGPT / GPT-4 / Gemini / any chat LLM:</b> paste the contents into your custom system prompt or "instructions" field. Done.</li>
<li><b>Agent runtime (LangChain, CrewAI, Anthropic SDK, OpenAI Assistants):</b> include as a system message or load via your runtime's skill / tool-context mechanism.</li>
<li><b>Just curious:</b> <code>curl https://gammaqc.com/skill/raw</code>.</li>
</ol>
</div>
<div class="preview">${escapeHtml(SKILL_MD.slice(0, 1200))}…</div>
<div class="foot">Skill v0.3.0 · free · portable across runtimes · <a href="/">back to gammaqc.com</a></div>
</main></body></html>`;
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "public, max-age=300",
      ...SECURITY_HEADERS,
    },
  });
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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
  async fetch(request, env) {
    globalThis.__env = env;   // make env reachable from sub-functions
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/" || path === "/index.html") return html(LANDING_HTML);
    if (path === "/healthz") {
      return new Response("ok", { status: 200, headers: { "content-type": "text/plain" } });
    }
    if (path === "/manifest.webmanifest") return json(MANIFEST);

    // ─── Gamma QC skill — downloadable LLM context file ──────────────
    // Drop-in for Claude (~/.claude/skills/gamma-qc/SKILL.md), or paste
    // as system prompt for any LLM agent. No login required.
    if (path === "/skill" || path === "/skill/") return renderSkillPage();
    if (path === "/skill/SKILL.md" || path === "/skill/download") {
      return new Response(SKILL_MD, {
        status: 200,
        headers: {
          "content-type": "text/markdown; charset=utf-8",
          "content-disposition": 'attachment; filename="SKILL.md"',
          "cache-control": "public, max-age=300",
          ...SECURITY_HEADERS,
        },
      });
    }
    if (path === "/skill/raw") {
      // Same content, no attachment header — for inline viewing or curl
      return new Response(SKILL_MD, {
        status: 200,
        headers: {
          "content-type": "text/markdown; charset=utf-8",
          "cache-control": "public, max-age=300",
          ...SECURITY_HEADERS,
        },
      });
    }

    // ─── Skill manifest — SRF-rail signed, dynamically discoverable ──
    // The elevation Commander asked for: the skill is no longer a static
    // markdown blob, it's a versioned + PQC-signable artifact on the same
    // Sovereign Review Fabric attestation chain that signs Gamma QC
    // analytical outputs. Agents can poll /skill/manifest.json to discover
    // the latest version, fetch only when the hash changes, and verify
    // the attestor signature offline before loading the content.
    if (path === "/skill/manifest.json") return handleSkillManifest(request);
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
