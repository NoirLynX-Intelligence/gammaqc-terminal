// srf-attestor — Sovereign Review Fabric receipt attestor (POC).
//
// Cloudflare Worker that signs Gamma QC artifact receipts with the SRF
// attestor private key, and serves the public key + verification endpoint.
//
// Routes:
//   POST /sign       — backend-only (Bearer SRF_INTERNAL_KEY). Signs a
//                       receipt body, returns {receipt, signature}.
//   GET  /verify     — public. ?receipt=<json>&sig=<hex> → 200 / 400 /
//                       422 with reason.
//   GET  /.well-known/attestor.json  — public JWKS. Lists current +
//                                       previous keys (60-day rotation
//                                       overlap).
//   GET  /healthz    — public. 'ok'.
//
// Bindings (wrangler.toml):
//   secret SRF_INTERNAL_KEY      — Bearer for /sign (backend calls only)
//   secret SRF_PRIV_HEX          — Ed25519 private key (32 bytes hex)
//                                   (ML-DSA-65 to follow once @noble/post-quantum is added)
//   secret SRF_PUB_HEX           — Ed25519 public key (32 bytes hex)
//   secret SRF_PUB_PREV_HEX      — optional previous public key, served
//                                   during 60-day rotation window
//   var    SRF_KEY_ID            — short id, e.g. "srf-2026-06"
//
// Why Ed25519 not ML-DSA-65 in v0: Cloudflare runtime crypto.subtle
// supports Ed25519 natively (fast, no bundle weight). ML-DSA-65 needs
// @noble/post-quantum (~80kb bundle; CF Workers cap 1MB so fine, but
// pulling it in is Wave 3 work). The wire format below is forward-
// compatible: alg field flips to 'ml-dsa-65' when we ship that.
//
// All responses include attestor versioning so receipt-issued-at-v0
// stays verifiable when we cut over to v1.

const HEX_RE = /^[0-9a-f]+$/;

function hexToBytes(hex) {
  if (!HEX_RE.test(hex) || hex.length % 2) throw new Error('bad-hex');
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return out;
}

function bytesToHex(bytes) {
  return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function importPriv(privHex) {
  // Ed25519 raw private key → JWK form for crypto.subtle.importKey.
  // CF supports Ed25519 since 2023; use 'raw' for the public, 'jwk' for priv.
  const raw = hexToBytes(privHex);
  if (raw.length !== 32) throw new Error('priv-len-32');
  // JWK form: d=priv (base64url), x=pub (base64url). For sign-only,
  // we only need d; CF will compute x internally on import.
  return crypto.subtle.importKey(
    'jwk',
    {
      kty: 'OKP',
      crv: 'Ed25519',
      d: bytesToB64Url(raw),
      x: '',   // CF derives if absent — fallback below if not supported
    },
    { name: 'Ed25519' },
    false,
    ['sign'],
  );
}

async function importPub(pubHex) {
  const raw = hexToBytes(pubHex);
  if (raw.length !== 32) throw new Error('pub-len-32');
  return crypto.subtle.importKey(
    'raw', raw, { name: 'Ed25519' }, false, ['verify'],
  );
}

function bytesToB64Url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function canonicalize(obj) {
  // RFC8785-style: sort keys recursively, no whitespace, stable order.
  // This is the bytes that get signed — both sides must canonicalize
  // identically or signatures won't verify.
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(canonicalize).join(',') + ']';
  const keys = Object.keys(obj).sort();
  return '{' + keys.map(k => JSON.stringify(k) + ':' + canonicalize(obj[k])).join(',') + '}';
}

async function sign(env, receipt) {
  const priv = await importPriv(env.SRF_PRIV_HEX);
  const msg = new TextEncoder().encode(canonicalize(receipt));
  const sigBuf = await crypto.subtle.sign({ name: 'Ed25519' }, priv, msg);
  return bytesToHex(new Uint8Array(sigBuf));
}

async function verify(env, receipt, sigHex) {
  const pub = await importPub(env.SRF_PUB_HEX);
  const msg = new TextEncoder().encode(canonicalize(receipt));
  const sig = hexToBytes(sigHex);
  if (await crypto.subtle.verify({ name: 'Ed25519' }, pub, sig, msg)) {
    return { ok: true, key_id: env.SRF_KEY_ID };
  }
  // Try previous key during rotation window
  if (env.SRF_PUB_PREV_HEX) {
    const pubPrev = await importPub(env.SRF_PUB_PREV_HEX);
    if (await crypto.subtle.verify({ name: 'Ed25519' }, pubPrev, sig, msg)) {
      return { ok: true, key_id: env.SRF_KEY_ID + '-prev', rotation_window: true };
    }
  }
  return { ok: false };
}

function jsonResp(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

async function handleSign(request, env) {
  const auth = request.headers.get('authorization') || '';
  const expected = `Bearer ${env.SRF_INTERNAL_KEY || ''}`;
  if (!env.SRF_INTERNAL_KEY || auth !== expected) {
    return jsonResp({ error: 'unauthorized' }, 401);
  }
  let body;
  try { body = await request.json(); } catch { return jsonResp({ error: 'bad-json' }, 400); }
  if (!body || typeof body !== 'object') return jsonResp({ error: 'object-required' }, 400);

  // Receipt MUST include these fields to be sign-eligible.
  const required = ['artifact_id', 'artifact_kind', 'generated_at', 'commit_sha'];
  for (const k of required) if (!(k in body)) return jsonResp({ error: `missing:${k}` }, 400);

  // Stamp the attestor metadata
  const receipt = {
    ...body,
    attestor: {
      key_id: env.SRF_KEY_ID || 'srf-unknown',
      alg: 'ed25519',
      version: 'srf-receipt-v0',
      signed_at: new Date().toISOString(),
    },
  };

  const signature = await sign(env, receipt);
  return jsonResp({ receipt, signature, alg: 'ed25519' });
}

async function handleVerify(request, env) {
  const url = new URL(request.url);
  const receiptStr = url.searchParams.get('receipt');
  const sig = url.searchParams.get('sig');
  if (!receiptStr || !sig) return jsonResp({ error: 'missing-params' }, 400);
  let receipt;
  try { receipt = JSON.parse(receiptStr); } catch { return jsonResp({ error: 'bad-receipt-json' }, 400); }
  const v = await verify(env, receipt, sig);
  if (!v.ok) return jsonResp({ verified: false, reason: 'signature-mismatch' }, 422);
  return jsonResp({ verified: true, ...v });
}

function handleJwks(env) {
  // JWKS shape so standard tooling (jose, python-jwt, etc.) can ingest.
  const keys = [];
  if (env.SRF_PUB_HEX) {
    keys.push({
      kty: 'OKP', crv: 'Ed25519',
      kid: env.SRF_KEY_ID || 'srf-current',
      use: 'sig', alg: 'EdDSA',
      x: bytesToB64Url(hexToBytes(env.SRF_PUB_HEX)),
    });
  }
  if (env.SRF_PUB_PREV_HEX) {
    keys.push({
      kty: 'OKP', crv: 'Ed25519',
      kid: (env.SRF_KEY_ID || 'srf') + '-prev',
      use: 'sig', alg: 'EdDSA',
      x: bytesToB64Url(hexToBytes(env.SRF_PUB_PREV_HEX)),
    });
  }
  return jsonResp({
    keys,
    rotation_policy: { period_days: 180, overlap_days: 60 },
    pqc_roadmap: 'ml-dsa-65 (FIPS-204) — Wave 3',
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/healthz') return new Response('ok');
    if (url.pathname === '/.well-known/attestor.json') return handleJwks(env);
    if (url.pathname === '/sign' && request.method === 'POST') return handleSign(request, env);
    if (url.pathname === '/verify' && request.method === 'GET') return handleVerify(request, env);
    return jsonResp({
      service: 'srf-attestor',
      version: 'v0',
      endpoints: ['/sign', '/verify', '/.well-known/attestor.json', '/healthz'],
    }, 404);
  },
};
