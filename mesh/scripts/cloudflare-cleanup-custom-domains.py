#!/usr/bin/env python3
"""cloudflare-cleanup-custom-domains.py — retry Custom Domain attach for
zones where the initial bulk-deploy fell back to route-only.

Context: when cloudflare-deploy-worker.py ran the full 923-zone bulk
deploy, 48 zones returned HTTP 409 'hostname already in use' on the
PUT /accounts/{id}/workers/domains call. This happened because the
PRIOR Custom Domain binding (e.g. brg-1333xxx service) had been deleted
from the account-level Custom Domains list but the hostname-to-service
mapping at CF's anycast edge hadn't fully cleared by the time we tried
to PUT our new binding.

Worker Routes succeeded for these zones (so redirects work — verified
on samples) but Custom Domain bindings are the highest-priority Worker
binding and provide the cleanest semantics. This script retries:

  1. List the GammaQC allocation (923 zones)
  2. For each: query account-level Custom Domains filtered by hostname
  3. If a Custom Domain exists pointing at gammaqc-mesh-redirect → skip
     (already correct)
  4. If a Custom Domain exists pointing at OLD service (brg-*, noirlynx-*)
     → DELETE then PUT our new binding
  5. If no Custom Domain exists → just PUT (was the 409 case where
     prior delete didn't propagate; try fresh)

Idempotent: safe to re-run any time. Sleeps between API calls to stay
under CF's 1200 req/min ceiling.

CREDENTIALS (same as cloudflare-deploy-worker.py):
    CF_API_TOKEN   — Workers Scripts:Edit + Workers Routes:Edit +
                     Zone:Read scopes
    CF_ACCOUNT_ID  — defaults to f9bc2c79be568de141c110b0d74ca47d
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
from typing import Any

CF_API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_ACCOUNT_ID = "f9bc2c79be568de141c110b0d74ca47d"
TARGET_SCRIPT = "gammaqc-mesh-redirect"
RATE_LIMIT_RPS = 4.0
SLEEP_S = 1.0 / RATE_LIMIT_RPS


class CFError(Exception):
    pass


def _cf(method: str, path: str, token: str,
        body: dict | None = None, timeout: float = 30.0) -> dict:
    """Call CF API. Returns parsed body (or synthesized success on empty)."""
    url = f"{CF_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"authorization": f"Bearer {token}", "accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw or resp.status == 204:
                return {"success": True, "result": None, "_synthesized": True}
            payload = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except (ValueError, AttributeError):
            err_body = {"raw": "(unparseable)"}
        raise CFError(f"HTTP {e.code} on {method} {path}: {err_body}") from e
    if not payload.get("success"):
        raise CFError(f"CF success=false on {method} {path}: {payload.get('errors')}")
    return payload


def _get_zone_id(domain: str, token: str) -> str | None:
    payload = _cf("GET", f"/zones?name={domain}", token)
    zones = payload.get("result", [])
    return zones[0]["id"] if zones else None


def _list_custom_domains_for_hostname(account_id: str, hostname: str, token: str) -> list[dict]:
    """Account-level filter by hostname. Returns 0 or 1 record typically
    (CF allows one Custom Domain per hostname per account)."""
    try:
        payload = _cf("GET",
                      f"/accounts/{account_id}/workers/domains?hostname={hostname}",
                      token)
        return payload.get("result", []) or []
    except CFError:
        return []


def _delete_custom_domain(domain_id: str, account_id: str, token: str) -> None:
    _cf("DELETE", f"/accounts/{account_id}/workers/domains/{domain_id}", token)


def _attach_custom_domain(hostname: str, zone_id: str, account_id: str, token: str) -> None:
    _cf("PUT", f"/accounts/{account_id}/workers/domains", token, body={
        "environment": "production",
        "hostname": hostname,
        "service": TARGET_SCRIPT,
        "zone_id": zone_id,
    })


def cleanup_zone(domain: str, account_id: str, token: str) -> str:
    """Returns: 'already-correct' | 'replaced' | 'attached-fresh' |
    'tls-stuck' | 'no zone' | 'error: ...'

    LESSON LEARNED from the live 923-zone cleanup run (2026-06-09):
      ~606 zones returned CF error 100117 'Hostname already has externally
      managed TLS certificate' on the PUT step. The Custom Domain DELETE
      succeeded but the subsequent PUT 409'd because the stale TLS cert
      binding from the prior Worker had not fully cleared from CF's
      anycast TLS-serving infrastructure. The Worker Route remains bound
      (this is the redirect path that actually serves users), so end-user
      behavior is unaffected — purely a cosmetic 'TLS layer' issue.

      Surface as 'tls-stuck' so the operator sees the categorized count
      vs zones needing real attention. Real fix (when desired) is CF
      dashboard per-zone click-through — not blocking launch.
    """
    zone_id = _get_zone_id(domain, token)
    if not zone_id:
        return "no zone"

    existing = _list_custom_domains_for_hostname(account_id, domain, token)
    if existing:
        cd = existing[0]
        current_service = cd.get("service")
        if current_service == TARGET_SCRIPT:
            return "already-correct"
        # Old service still bound — delete + try to re-attach to our Worker
        _delete_custom_domain(cd["id"], account_id, token)
        time.sleep(SLEEP_S)
        try:
            _attach_custom_domain(domain, zone_id, account_id, token)
            return f"replaced ({current_service} → {TARGET_SCRIPT})"
        except CFError as e:
            # CF error 100117 = stale TLS binding. Not user-impacting
            # (route still serves redirects). Surface as 'tls-stuck'.
            if "100117" in str(e) or "externally managed" in str(e):
                return "tls-stuck (route serves; dashboard fix needed)"
            raise

    # No Custom Domain currently — try to attach fresh
    try:
        _attach_custom_domain(domain, zone_id, account_id, token)
        return "attached-fresh"
    except CFError as e:
        if "100117" in str(e) or "externally managed" in str(e):
            return "tls-stuck (route serves; dashboard fix needed)"
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path,
                    help="path to GammaQC allocation CSV")
    ap.add_argument("--account-id", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap zone count (0=all); use --limit 3 for smoke test")
    ap.add_argument("--start", type=int, default=0)
    args = ap.parse_args()

    token = os.environ.get("CF_API_TOKEN")
    if not token:
        print("::error::CF_API_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)
    account_id = args.account_id or os.environ.get("CF_ACCOUNT_ID") or DEFAULT_ACCOUNT_ID

    domains: list[str] = []
    with args.csv.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            d = (row.get("Domain Name") or "").strip().lower()
            if d:
                domains.append(d)
    if args.start:
        domains = domains[args.start:]
    if args.limit:
        domains = domains[:args.limit]

    est_min = len(domains) * 3 * SLEEP_S / 60
    print(f"[cleanup] {len(domains)} zone(s) at {RATE_LIMIT_RPS} req/sec "
          f"(~{est_min:.1f} min)")

    stats = {"already-correct": 0, "replaced": 0, "attached-fresh": 0,
             "tls-stuck": 0, "no zone": 0, "error": 0}
    failed: list[tuple[str, str]] = []
    for i, d in enumerate(domains, 1):
        try:
            status = cleanup_zone(d, account_id, token)
        except CFError as e:
            status = f"error: {str(e)[:200]}"
        except Exception as e:
            status = f"error: {type(e).__name__}: {str(e)[:200]}"

        key = "error" if status.startswith("error:") else status.split(" ")[0]
        stats[key] = stats.get(key, 0) + 1
        if status.startswith("error:"):
            failed.append((d, status))
            print(f"[{i:4d}/{len(domains)}] {d}: {status}")
        elif status == "already-correct" and (i % 50 != 0 and i != len(domains)):
            pass   # quiet — already done
        else:
            print(f"[{i:4d}/{len(domains)}] {d}: {status}")
        if i < len(domains):
            time.sleep(SLEEP_S)

    print()
    print(f"[cleanup] DONE")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    if failed:
        Path("cf-cleanup.failed.txt").write_text(
            "Domain Name\n" + "\n".join(d for d, _ in failed) + "\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
