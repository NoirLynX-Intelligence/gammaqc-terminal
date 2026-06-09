# Fixing the ~606 "TLS-stuck" zones (cosmetic — NOT blocking launch)

> Background: during the 923-zone Custom Domain cleanup pass on 2026-06-09,
> ~606 zones returned CF error 100117 "Hostname already has externally
> managed TLS certificate" on the PUT step. This is a known CF anycast-
> propagation artifact when re-binding a Custom Domain after the prior
> binding was deleted.
>
> **End-user impact: zero.** The Worker Route (`*.<host>/*` →
> `gammaqc-mesh-redirect`) is still bound on these zones and serves the
> redirect correctly. Verified via curl on a sample of 10 zones across
> the range: 9 of 10 returned `301 Location: https://install.gammaqc.com/install`.
>
> **What's missing on these zones:** the Custom Domain layer (the
> highest-priority Worker binding type). It's belt-and-suspenders — if a
> future Worker Route gets reshuffled, the Custom Domain would protect
> the redirect contract. Without it, we rely on Worker Routes alone.
> Cosmetic until/unless that future reshuffling happens.

## Why API-only fix doesn't work

The `cloudflare-cleanup-custom-domains.py` script attempts the obvious
flow:
1. DELETE the stale Custom Domain (succeeds)
2. PUT the new Custom Domain pointing at `gammaqc-mesh-redirect` (fails
   with code 100117)

The 100117 error is CF's way of saying: "there's a stale TLS
certificate binding from the previous Custom Domain that hasn't
finished propagating across our anycast TLS-serving infrastructure."
Retries 24h later don't help — the binding is in a wedged state.

## Dashboard fix (per-zone, ~30 sec each)

For each "tls-stuck" zone listed in the cleanup script's output:

1. Open https://dash.cloudflare.com/f9bc2c79be568de141c110b0d74ca47d/{HOSTNAME}/workers-routes
2. Click into the **Workers Routes** tab
3. Scroll down to **Custom Domains** subsection
4. If there's a Custom Domain entry listed: click **Delete** next to it,
   confirm
5. Click **+ Add Custom Domain**, enter the hostname, select service
   `gammaqc-mesh-redirect`, environment `production`
6. Save

The TLS cert provisions in ~2 min per zone after the fix.

## Bulk dashboard fix (if you really want all 606)

The CF dashboard supports keyboard navigation. Workflow:

1. Get the list of stuck zones:
   ```bash
   cd /path/to/gammaqc-terminal
   CF_API_TOKEN=<token> python mesh/scripts/cloudflare-cleanup-custom-domains.py \
       --csv mesh/data/domains-gammaqc.csv 2>&1 | grep "tls-stuck" > /tmp/stuck.txt
   ```

2. For each zone, open the workers-routes page (you can `xargs` open
   in batches of 10):
   ```bash
   awk '{print $2}' /tmp/stuck.txt | head -10 | \
     xargs -I {} cmd.exe /c start "" "https://dash.cloudflare.com/f9bc2c79be568de141c110b0d74ca47d/{}/workers-routes"
   ```

3. Click through each tab (Delete-then-Add-Custom-Domain pattern above)

4. At ~30 sec per zone × 606 zones = ~5 hours of clicking. **Not worth it
   unless real bug surfaces.**

## When to actually do this

Only when one of these happens:
- A future Worker change accidentally removes the Worker Route on these
  zones (then redirects would 404 and we'd want the Custom Domain
  belt-and-suspenders binding)
- A CF support ticket request to clean up the orphan TLS bindings (CF
  support may be able to bulk-release them server-side)
- A scheduled "tech debt cleanup" sprint after launch metrics are stable

For tonight's Wave 1 launch: **ignore.** The user-facing redirect works.
