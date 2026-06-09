#!/usr/bin/env python3
"""cloudflare-bulk-redirect.py — flip all 923 GammaQC mesh nodes into 301
redirects to install.gammaqc.com via Cloudflare's Rulesets API.

Why this script exists:
    The 923-domain GammaQC mesh allocation already lives on Cloudflare
    (per Commander 2026-06-09). Rather than pointing 923 A-records at the
    Hostinger box + having nginx serve 923 redirects, we leverage
    Cloudflare's edge to do the redirect there — faster, free SSL on every
    domain, zero Hostinger load for mesh traffic.

What it does (per domain in domains-gammaqc.csv):
    1. Verifies the zone exists in the Cloudflare account
    2. Enables 'Always Use HTTPS' so http:// hits also get the redirect
    3. Creates a Single Redirect rule in the http_request_dynamic_redirect
       phase:
         expression: (http.request.uri.path matches ".*")
         action:     redirect
         target:     concat("https://install.gammaqc.com", http.request.uri.path,
                            if(len(http.request.uri.query) > 0,
                               concat("?", http.request.uri.query), ""))
         status:     301
    4. Updates the redirect's description with a version stamp so we can
       grep/sweep them later

Idempotent: re-running on a domain that already has the redirect just
updates the description (Cloudflare returns 200 with the existing rule;
we PATCH it).

CREDENTIALS — set as env vars:
    CF_API_TOKEN       — Cloudflare API token, scope: Zone.DNS + Zone.Page Rules
                         + Zone.Zone Settings + Account.Account Rulesets (Read)
                         + Zone (Read).
                         Generate at: https://dash.cloudflare.com/profile/api-tokens
                         Use "Create Custom Token" → grant the scopes above
                         on "All zones" (or specifically the GammaQC ones).

Rate limit: Cloudflare allows 1200 API calls per 5 min per token. This
script makes ~4 calls per domain (zone lookup + settings update +
ruleset list + rule create). 923 × 4 = ~3700 calls → must throttle to
4 calls/sec to stay under limit.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Tuple


CF_API_BASE = "https://api.cloudflare.com/client/v4"
RATE_LIMIT_RPS = 4.0
SLEEP_BETWEEN_CALLS_S = 1.0 / RATE_LIMIT_RPS
SCRIPT_VERSION = "gammaqc-mesh/v0.3 (bulk-redirect)"


class CloudflareError(Exception):
    pass


def _env_or_die(*names: str) -> Tuple[str, ...]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print(f"::error::missing env var(s): {missing}", file=sys.stderr)
        for n in missing:
            print(f"  set with: export {n}=<value>", file=sys.stderr)
        sys.exit(1)
    return tuple(os.environ[n] for n in names)


def _cf_request(method: str, path: str, token: str,
                body: dict | None = None, timeout: float = 30.0) -> dict:
    """Call the Cloudflare API. Raises CloudflareError on non-success."""
    url = f"{CF_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except (ValueError, AttributeError):
            err_body = {"raw": "(unparseable error body)"}
        raise CloudflareError(f"HTTP {e.code} on {method} {path}: {err_body}") from e
    if not payload.get("success"):
        raise CloudflareError(f"CF returned success=false on {method} {path}: "
                              f"{payload.get('errors')}")
    return payload


def _read_domains(csv_path: Path) -> list[str]:
    domains: list[str] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "Domain Name" not in (reader.fieldnames or []):
            raise SystemExit(f"::error::{csv_path}: missing 'Domain Name' column")
        for row in reader:
            d = (row.get("Domain Name") or "").strip().lower()
            if d:
                domains.append(d)
    return domains


def _get_zone_id(domain: str, token: str) -> str | None:
    """Look up the CF zone ID for a domain. Returns None if zone not found."""
    payload = _cf_request("GET", f"/zones?name={domain}", token)
    zones = payload.get("result", [])
    return zones[0]["id"] if zones else None


def _enable_always_https(zone_id: str, token: str) -> None:
    """Enable 'Always Use HTTPS' setting so http:// requests redirect to https://
    BEFORE our redirect rule fires (otherwise an http://1333077.xyz hit gets the
    301 to https://install.gammaqc.com, which works but adds a hop)."""
    try:
        _cf_request("PATCH", f"/zones/{zone_id}/settings/always_use_https",
                    token, body={"value": "on"})
    except CloudflareError as e:
        # Some plans don't expose this setting via API; not fatal
        print(f"  (warn: couldn't set always_use_https: {str(e)[:120]})")


def _get_dynamic_redirect_ruleset_id(zone_id: str, token: str) -> str:
    """Find or create the dynamic redirect ruleset for this zone."""
    payload = _cf_request("GET", f"/zones/{zone_id}/rulesets", token)
    for rs in payload.get("result", []):
        if rs.get("phase") == "http_request_dynamic_redirect":
            return rs["id"]
    # Doesn't exist → create one (empty ruleset)
    created = _cf_request("POST", f"/zones/{zone_id}/rulesets", token, body={
        "name": "gammaqc-mesh-redirect",
        "kind": "zone",
        "phase": "http_request_dynamic_redirect",
        "rules": [],
        "description": SCRIPT_VERSION,
    })
    return created["result"]["id"]


def _upsert_redirect_rule(zone_id: str, ruleset_id: str,
                          token: str, target_canonical: str) -> None:
    """Add OR update the redirect rule. We identify our rule by description
    prefix ('gammaqc-mesh:') so re-runs replace cleanly instead of
    creating duplicates."""
    # List existing rules
    payload = _cf_request("GET",
                          f"/zones/{zone_id}/rulesets/{ruleset_id}",
                          token)
    existing = payload.get("result", {}).get("rules", []) or []
    our_rule_id: str | None = None
    other_rules = []
    for r in existing:
        desc = r.get("description", "")
        if desc.startswith("gammaqc-mesh:"):
            our_rule_id = r.get("id")
        else:
            other_rules.append(r)

    # Build the redirect rule. The expression matches every request
    # (anything that arrives at this zone gets redirected). The target
    # preserves the original path + query string.
    new_rule = {
        "action": "redirect",
        "expression": '(http.request.uri.path matches ".*")',
        "description": f"gammaqc-mesh: 301 -> {target_canonical} ({SCRIPT_VERSION})",
        "enabled": True,
        "action_parameters": {
            "from_value": {
                "status_code": 301,
                "target_url": {
                    "expression": (
                        f'concat("https://{target_canonical}", '
                        f'http.request.uri.path, '
                        f'if(len(http.request.uri.query) > 0, '
                        f'concat("?", http.request.uri.query), ""))'
                    ),
                },
                "preserve_query_string": True,
            },
        },
    }
    if our_rule_id:
        new_rule["id"] = our_rule_id

    # PUT replaces the whole ruleset.rules — include our rule + any others
    updated_rules = other_rules + [new_rule]
    _cf_request("PUT", f"/zones/{zone_id}/rulesets/{ruleset_id}", token, body={
        "rules": updated_rules,
    })


def setup_domain(domain: str, token: str, target_canonical: str) -> str:
    """Returns a status string ('ok', 'no zone', 'error: ...')."""
    zone_id = _get_zone_id(domain, token)
    if not zone_id:
        return "no zone"
    _enable_always_https(zone_id, token)
    time.sleep(SLEEP_BETWEEN_CALLS_S)
    ruleset_id = _get_dynamic_redirect_ruleset_id(zone_id, token)
    time.sleep(SLEEP_BETWEEN_CALLS_S)
    _upsert_redirect_rule(zone_id, ruleset_id, token, target_canonical)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path,
                    help="path to GammaQC allocation CSV (from extract-gammaqc-domains.py)")
    ap.add_argument("--canonical", default="install.gammaqc.com",
                    help="redirect target hostname (default: install.gammaqc.com)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap how many domains to process (0=all); use --limit 3 for first-pass smoke")
    ap.add_argument("--start", type=int, default=0,
                    help="skip the first N domains (resume after partial run)")
    args = ap.parse_args()

    (token,) = _env_or_die("CF_API_TOKEN")
    domains = _read_domains(args.csv)
    if args.start:
        domains = domains[args.start:]
    if args.limit:
        domains = domains[:args.limit]

    est_min = len(domains) * 4 * SLEEP_BETWEEN_CALLS_S / 60   # ~4 calls per domain
    print(f"[cf-bulk-redirect] {len(domains)} domain(s) to process at "
          f"{RATE_LIMIT_RPS} req/sec (~{est_min:.1f} min)")
    print(f"[cf-bulk-redirect] target: https://{args.canonical}{{path}}")

    succeeded = 0
    no_zone: list[str] = []
    failed: list[Tuple[str, str]] = []
    for i, d in enumerate(domains, 1):
        try:
            status = setup_domain(d, token, args.canonical)
        except CloudflareError as e:
            status = f"error: {str(e)[:200]}"
        except Exception as e:
            status = f"error: {type(e).__name__}: {str(e)[:200]}"
        if status == "ok":
            succeeded += 1
            print(f"[{i:4d}/{len(domains)}] {d}: OK")
        elif status == "no zone":
            no_zone.append(d)
            print(f"[{i:4d}/{len(domains)}] {d}: NO ZONE (not on this CF account?)")
        else:
            failed.append((d, status))
            print(f"[{i:4d}/{len(domains)}] {d}: {status}")
        # Inter-domain throttle (we already sleep within setup_domain too)
        if i < len(domains):
            time.sleep(SLEEP_BETWEEN_CALLS_S)

    print()
    print(f"[cf-bulk-redirect] DONE")
    print(f"  succeeded   : {succeeded}")
    print(f"  no zone     : {len(no_zone)}  (domain not present in this CF account)")
    print(f"  failed      : {len(failed)}")
    if no_zone:
        out = "cf-bulk-redirect.no-zone.txt"
        Path(out).write_text("\n".join(no_zone) + "\n")
        print(f"  no-zone list written to {out}")
    if failed:
        out = "cf-bulk-redirect.failed.txt"
        Path(out).write_text("Domain Name\n" + "\n".join(d for d, _ in failed) + "\n")
        print(f"  failed list  written to {out}")
        sys.exit(2)


if __name__ == "__main__":
    main()
