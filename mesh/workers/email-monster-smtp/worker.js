/**
 * email-monster-smtp — sovereign outbound email Worker (CF native transport).
 *
 * Drop-in replacement for the previous Worker that relied on MailChannels
 * (free tier ended mid-2024) or pre-verified CF Send Email binding
 * destinations (only works for admin-side addresses, not customer signups).
 *
 * This version uses Cloudflare's native Email Sending REST API
 * (public beta since 2026-04-16, SMTP submission added 2026-06-08):
 *   POST https://api.cloudflare.com/client/v4/accounts/{id}/email/sending/send
 *
 * Same contract documented in the gammaqc-backend integration spec —
 * callers do NOT need to change anything:
 *
 *   POST https://email-monster-smtp.drklynx-os.workers.dev/api/email/send-internal
 *   Headers:
 *     content-type: application/json
 *     x-internal-auth: <INTERNAL_SHARED_SECRET>
 *   Body:
 *     {from, to, subject, text, html, from_name?, reply_to?, tag?}
 *   Returns:
 *     200 {success: true, send_id, transport, from, to, tag}
 *     403 {error: "Internal auth required"}
 *     400 {error: "Missing required fields", required: [...]}
 *     403 {error: "sender domain is not a registered sovereign tenant"}
 *     502 {success: false, transport_error: "..."}
 *
 * PREREQUISITES (one-time, do these in CF dashboard):
 *   1. Enable Email Service > Email Sending on the account (Workers Paid plan)
 *   2. Onboard EACH sender domain (gammaqc.com, drklynx.com, etc.) under
 *      Email Service > Email Sending > Domains. Add the DKIM/MX records
 *      Cloudflare provides — without onboarding, the REST API rejects
 *      with "domain not configured".
 *   3. Create a CF API token with "Email Sending: Edit" permission scoped
 *      to the account. Token value goes in this Worker's CF_EMAIL_TOKEN secret.
 *
 * SECRETS TO SET (wrangler secret put):
 *   - INTERNAL_SHARED_SECRET   (same as drklynx-auth, drklynx-domains,
 *                                microhost-provisioner, gammaqc-backend)
 *   - CF_EMAIL_TOKEN           (the CF API token with Email Sending: Edit)
 *   - CF_ACCOUNT_ID            (the account UUID — currently
 *                                f9bc2c79be568de141c110b0d74ca47d)
 *
 * BINDINGS TO ADD (wrangler.toml):
 *   - D1: MONSTER_VAULT_DB     (the monster-vault-db D1 for ledger writes)
 *     OPTIONAL — Worker degrades gracefully if absent; just skips ledger.
 */

const TRANSPORT_NAME = "cf-native-email-sending";
const CF_EMAIL_ENDPOINT = (accountId) =>
  `https://api.cloudflare.com/client/v4/accounts/${accountId}/email/sending/send`;

// Sender-domain allow-list. The CF native API enforces onboarding too,
// but we layer this here to fail fast + return the structured error your
// callers already handle (per the existing integration spec).
const TENANT_DOMAINS = new Set([
  "gammaqc.com",
  "drklynx.com",
  // Add more as you onboard them in CF dashboard.
]);

const REQUIRED_FIELDS = ["from", "to", "subject"];

function uuid() {
  // RFC 4122 v4 via crypto.randomUUID (Workers runtime supports it)
  return crypto.randomUUID();
}

function isAllowedSender(fromAddress) {
  const at = fromAddress.lastIndexOf("@");
  if (at < 0) return false;
  const domain = fromAddress.slice(at + 1).toLowerCase();
  return TENANT_DOMAINS.has(domain);
}

function asResponse(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function writeLedger(env, row) {
  // Best-effort. Never fail the send because the ledger is down.
  if (!env.MONSTER_VAULT_DB) return;
  try {
    await env.MONSTER_VAULT_DB.prepare(
      `INSERT INTO email_sends_ledger
        (id, from_address, from_domain, to_count, subject, tag,
         transport, ok, status_or_error, sent_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        row.id, row.from_address, row.from_domain, row.to_count,
        (row.subject || "").slice(0, 200), row.tag || null,
        row.transport, row.ok ? 1 : 0,
        (row.status_or_error || "").slice(0, 500),
        row.sent_at,
      )
      .run();
  } catch (e) {
    console.warn(`[ledger] write failed (non-fatal): ${e.message || e}`);
  }
}

addEventListener("fetch", (event) => {
  event.respondWith(handle(event.request, globalThis.__env || event.env));
});

// Module-syntax export for environments where addEventListener isn't
// the default. Workers runtime supports both.
export default {
  async fetch(request, env, ctx) {
    return handle(request, env);
  },
};

async function handle(request, env) {
  if (request.method !== "POST") {
    return asResponse(405, { error: "method not allowed" });
  }
  const url = new URL(request.url);
  if (url.pathname !== "/api/email/send-internal") {
    return asResponse(404, { error: "not found" });
  }

  // 1. Internal auth
  const auth = request.headers.get("x-internal-auth");
  if (!auth || auth !== env.INTERNAL_SHARED_SECRET) {
    return asResponse(403, { error: "Internal auth required" });
  }

  // 2. Parse body
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return asResponse(400, { error: "invalid JSON body" });
  }

  // 3. Required-field check
  const missing = REQUIRED_FIELDS.filter((f) => !body[f]);
  if (missing.length > 0) {
    return asResponse(400, { error: "Missing required fields", required: missing });
  }
  if (!body.text && !body.html) {
    return asResponse(400, {
      error: "Missing required fields",
      required: ["text or html (at least one)"],
    });
  }

  // 4. Sender-domain allow-list
  if (!isAllowedSender(body.from)) {
    return asResponse(403, {
      error: "sender domain is not a registered sovereign tenant",
      domain: body.from.split("@").pop(),
    });
  }

  // 5. Build CF native Email Sending REST API payload. The shape maps
  // 1:1 with their EmailMessageBuilder fields.
  const cfPayload = {
    from: body.from_name
      ? { email: body.from, name: body.from_name }
      : body.from,
    to: body.to,
    subject: body.subject,
  };
  if (body.text) cfPayload.text = body.text;
  if (body.html) cfPayload.html = body.html;
  if (body.reply_to) cfPayload.replyTo = body.reply_to;
  if (body.headers) cfPayload.headers = body.headers;

  // 6. Call CF Email Sending REST API
  const accountId = env.CF_ACCOUNT_ID;
  const cfToken = env.CF_EMAIL_TOKEN;
  if (!accountId || !cfToken) {
    return asResponse(500, {
      error: "CF Email Sending not configured (CF_ACCOUNT_ID or CF_EMAIL_TOKEN missing)",
    });
  }

  const sendId = uuid();
  const sentAt = new Date().toISOString();
  const fromDomain = body.from.split("@").pop().toLowerCase();
  const toCount = Array.isArray(body.to) ? body.to.length : 1;

  let cfResp;
  try {
    cfResp = await fetch(CF_EMAIL_ENDPOINT(accountId), {
      method: "POST",
      headers: {
        "authorization": `Bearer ${cfToken}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(cfPayload),
    });
  } catch (e) {
    const err = `network: ${e.message || e}`;
    await writeLedger(env, {
      id: sendId, from_address: body.from, from_domain: fromDomain,
      to_count: toCount, subject: body.subject, tag: body.tag,
      transport: TRANSPORT_NAME, ok: false, status_or_error: err,
      sent_at: sentAt,
    });
    return asResponse(502, { success: false, transport_error: err });
  }

  let cfBody = null;
  try {
    cfBody = await cfResp.json();
  } catch (e) {
    // Empty or non-JSON response — treat as opaque
    cfBody = { raw: "non-json response" };
  }

  if (cfResp.ok && cfBody.success !== false) {
    // CF accepted the send. Their response includes a message id.
    await writeLedger(env, {
      id: sendId, from_address: body.from, from_domain: fromDomain,
      to_count: toCount, subject: body.subject, tag: body.tag,
      transport: TRANSPORT_NAME, ok: true,
      status_or_error: `cf-message-id:${(cfBody.result && cfBody.result.id) || "n/a"}`,
      sent_at: sentAt,
    });
    return asResponse(200, {
      success: true,
      send_id: sendId,
      transport: TRANSPORT_NAME,
      from: body.from,
      to: Array.isArray(body.to) ? body.to : [body.to],
      tag: body.tag || null,
      cf_message_id: (cfBody.result && cfBody.result.id) || null,
    });
  }

  // Transport failure — CF rejected the send
  const errMsg =
    (cfBody.errors && cfBody.errors[0] && cfBody.errors[0].message) ||
    cfBody.error ||
    `HTTP ${cfResp.status}`;
  await writeLedger(env, {
    id: sendId, from_address: body.from, from_domain: fromDomain,
    to_count: toCount, subject: body.subject, tag: body.tag,
    transport: TRANSPORT_NAME, ok: false,
    status_or_error: errMsg, sent_at: sentAt,
  });
  return asResponse(502, {
    success: false,
    transport: TRANSPORT_NAME,
    transport_error: errMsg,
    cf_status: cfResp.status,
  });
}
