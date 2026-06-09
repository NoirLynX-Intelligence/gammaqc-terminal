# email-monster-smtp Worker — CF-native transport upgrade

> Drop-in replacement for the previous email-monster-smtp Worker that
> relied on the MailChannels free tier (ended mid-2024) or pre-verified
> CF Send Email binding destinations (admin-only).
>
> This version uses **Cloudflare's native Email Sending REST API**
> (public beta since 2026-04-16, [docs](https://developers.cloudflare.com/email-service/api/send-emails/rest-api/)),
> which delivers to ANY recipient and supports HTML, plain text,
> attachments, custom headers, automatic DKIM + ARC signing, and shared
> dashboard logs.

## What this Worker does

Same contract the existing 4 callers (`drklynx-auth`, `drklynx-domains`,
`microhost-provisioner`, `gammaqc-backend`) already use — **zero caller
changes required**:

```
POST https://email-monster-smtp.drklynx-os.workers.dev/api/email/send-internal
Headers:
  content-type: application/json
  x-internal-auth: <INTERNAL_SHARED_SECRET>
Body:
  {
    "from": "support@gammaqc.com",
    "from_name": "Resonance",
    "to": "user@example.com",
    "subject": "Welcome",
    "text": "...",
    "html": "<p>...</p>",
    "reply_to": "noreply@gammaqc.com",
    "tag": "ledger-correlation-id"
  }
Returns:
  200 { success, send_id, transport: "cf-native-email-sending", from, to, tag, cf_message_id }
  403 { error: "Internal auth required" }
  400 { error: "Missing required fields", required: [...] }
  403 { error: "sender domain is not a registered sovereign tenant", domain }
  502 { success: false, transport_error: "..." }
```

## Prerequisites (one-time setup)

These steps unblock the new transport — without them, deployment
succeeds but every send returns 502.

### 1. Enable Email Service > Email Sending on the account

CF dashboard → Email Service → Email Sending → **Enable** (requires
Workers Paid plan — $5/mo).

### 2. Onboard each sender domain

For each domain you'll send `from:` (gammaqc.com, drklynx.com, etc.):

- Email Service → Email Sending → **Domains** → **Add domain**
- Add the DKIM CNAME records CF provides to the domain's DNS
- Add the SPF TXT record (CF may add automatically since DNS is on CF)
- Wait for validation (typically <5 min for CF-DNS-hosted domains)

The Worker has a hardcoded `TENANT_DOMAINS` allow-list in `worker.js`
matching what's onboarded. Update both in sync when adding domains.

### 3. Create the CF API token

Dashboard → My Profile → API Tokens → **Create Token** → Custom token:

- **Permissions:** `Account` → `Email Sending` → `Edit`
- **Resources:** Specific account → your account
- **Client IP filter:** leave blank (Worker authenticates via its own credentials)
- Copy the token immediately — shown only once.

### 4. Set Worker secrets

From the Worker's project directory:

```bash
wrangler secret put INTERNAL_SHARED_SECRET   # same value as the other 4 Workers
wrangler secret put CF_EMAIL_TOKEN           # the token from step 3
wrangler secret put CF_ACCOUNT_ID            # f9bc2c79be568de141c110b0d74ca47d
```

### 5. (Optional) Wire the ledger D1

If you want email_sends_ledger writes:

```bash
wrangler d1 list   # find monster-vault-db's UUID
# Add it to wrangler.toml under [[d1_databases]].database_id
```

The Worker checks for `env.MONSTER_VAULT_DB` before writing — absent
binding = silent skip. No breakage if you defer the ledger.

## Deploy

```bash
cd path/to/email-monster-smtp   # the worker.js + wrangler.toml from this dir
npm install -g wrangler          # if not installed
wrangler login                   # OAuth flow once per machine
wrangler deploy
```

Wrangler reports the URL on success — should be
`https://email-monster-smtp.drklynx-os.workers.dev` (matches the
integration spec callers expect).

## Verify

From any machine with `INTERNAL_SHARED_SECRET` available:

```bash
# 1. Auth failure path (expect 403)
curl -X POST https://email-monster-smtp.drklynx-os.workers.dev/api/email/send-internal \
  -H 'content-type: application/json' \
  -H 'x-internal-auth: wrong-secret' \
  -d '{"from":"support@gammaqc.com","to":"x@x.com","subject":"t","text":"t"}'

# 2. Validation failure (expect 400)
curl -X POST https://email-monster-smtp.drklynx-os.workers.dev/api/email/send-internal \
  -H 'content-type: application/json' \
  -H "x-internal-auth: $INTERNAL_SHARED_SECRET" \
  -d '{"from":"support@gammaqc.com"}'

# 3. Sender-domain rejection (expect 403)
curl -X POST https://email-monster-smtp.drklynx-os.workers.dev/api/email/send-internal \
  -H 'content-type: application/json' \
  -H "x-internal-auth: $INTERNAL_SHARED_SECRET" \
  -d '{"from":"hi@randomexample.com","to":"x@x.com","subject":"t","text":"t"}'

# 4. Real send (expect 200 with send_id + cf_message_id)
curl -X POST https://email-monster-smtp.drklynx-os.workers.dev/api/email/send-internal \
  -H 'content-type: application/json' \
  -H "x-internal-auth: $INTERNAL_SHARED_SECRET" \
  -d '{"from":"support@gammaqc.com","to":"your-test-inbox@example.com","subject":"CF native test","text":"Hello from CF Email Sending REST API"}'
```

If step 4 lands in your inbox, you're done. Backend integrations
(gammaqc-backend's webhook auto-provision email path) start working
immediately — no backend changes required.

## Why CF native vs Resend/SES/Postmark

| | CF native | Resend | SES |
|---|---|---|---|
| Cost | included in Workers Paid ($5/mo) | $0 first 100/day, then $20/50k | $0.10/1k |
| DKIM/ARC | automatic | automatic | manual |
| Same-cloud-as-zones | ✅ | ❌ | ❌ |
| Cross-cloud sniff | none (CF→CF) | DNS lookup + new vendor relationship | DNS + AWS account |
| Failure surface | CF API outage | Resend outage | AWS outage |
| Time-to-wire | 30 min | 1 hour | 2-3 hours |

For GammaQC's specific case (923 zones already on CF, gammaqc.com on CF
DNS already, Workers Paid plan likely already active for the existing
80+ Workers), CF native is the obvious choice.

## What happens to the existing Worker code

The previous `worker.js` (which tried MailChannels + falls back to CF
Send Email binding for verified destinations) becomes obsolete the
moment this version deploys. You can keep the old code in git history;
the new version is a strict superset of capability with no caller-facing
contract changes.

The integration-spec contract (endpoint URL, auth header, body shape,
response shape) is byte-for-byte identical so the 4 existing callers
continue to work without redeployment.

## Limitations (CF-side, not Worker-side)

- **Max message size:** 5 MiB (including attachments). Our transactional
  emails are <10 KiB, comfortably under.
- **Rate limits:** see [CF docs](https://developers.cloudflare.com/email-service/platform/limits/);
  effectively unlimited for typical transactional volume (well below
  the threshold for bulk-sender behavior).
- **Reputation:** CF manages the sending IPs + reputation. New domains
  warm up automatically as you send — no manual IP warmup process.
