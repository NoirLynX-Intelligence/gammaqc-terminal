#!/usr/bin/env python3
"""cloudflare-deploy-worker.py — deploy the gammaqc-mesh-redirect Worker
once at account level, then bind it to all 923 GammaQC mesh zones.

Replaces ANY existing Worker route on each zone (the older broken Workers
returning 'Cannot read properties of undefined' 500s) with a route pointing
at our redirect script.

Architecture:
    POST/PUT /accounts/{account_id}/workers/scripts/{script_name}
        Upload the Worker once at account level.
    GET  /zones/{zone_id}/workers/routes
        List existing routes (to detect what to delete).
    DELETE /zones/{zone_id}/workers/routes/{route_id}
        Remove old broken routes.
    POST /zones/{zone_id}/workers/routes
        Bind our route to the zone with pattern *<domain>/*.

Idempotent: re-running just re-uploads the script (same content) and
re-binds the routes (deleting+recreating). No duplicates, no leaks.

CREDENTIALS:
    CF_API_TOKEN   — Cloudflare API token. Required permissions:
                       - Account : Workers Scripts : Edit
                       - Zone : Workers Routes : Edit
                       - Zone : Zone : Read
    CF_ACCOUNT_ID  — Cloudflare account ID. Defaults to the gammaqc
                       account ID (f9bc2c79be568de141c110b0d74ca47d).

Rate limit: CF allows 1200 req/min per token. Each zone needs:
    1 GET routes + (maybe) 1 DELETE old + 1 POST new = 2-3 calls/zone.
At 923 zones × ~3 calls = ~2770 calls → throttle to 4 req/sec → ~12 min.
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
SCRIPT_NAME = "gammaqc-mesh-redirect"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "workers" / "redirect.js"
RATE_LIMIT_RPS = 4.0
SLEEP_S = 1.0 / RATE_LIMIT_RPS


class CFError(Exception):
    pass


def _cf(method: str, path: str, token: str,
        body: dict | None = None,
        raw_body: bytes | None = None,
        content_type: str = "application/json",
        timeout: float = 30.0) -> dict:
    """Call CF API. Returns parsed response body. Raises CFError on non-success."""
    url = f"{CF_API_BASE}{path}"
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
    else:
        data = None
    headers = {
        "authorization": f"Bearer {token}",
        "accept": "application/json",
    }
    if data is not None:
        headers["content-type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status_code = resp.status
            # CF DELETE endpoints return 200 with empty body OR 204 No Content.
            # Treat empty body as implicit success — there's nothing to parse.
            if not raw or status_code == 204:
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


def upload_worker_script(token: str, account_id: str, script_text: str) -> None:
    """PUT the script to /accounts/{account_id}/workers/scripts/{script_name}.
    Service Worker syntax → Content-Type: application/javascript."""
    path = f"/accounts/{account_id}/workers/scripts/{SCRIPT_NAME}"
    _cf("PUT", path, token,
        raw_body=script_text.encode("utf-8"),
        content_type="application/javascript")
    print(f"[deploy] uploaded Worker '{SCRIPT_NAME}' to account {account_id[:8]}…")


def _get_zone_id(domain: str, token: str) -> str | None:
    payload = _cf("GET", f"/zones?name={domain}", token)
    zones = payload.get("result", [])
    return zones[0]["id"] if zones else None


def _list_routes(zone_id: str, token: str) -> list[dict]:
    payload = _cf("GET", f"/zones/{zone_id}/workers/routes", token)
    return payload.get("result", []) or []


def _delete_route(zone_id: str, route_id: str, token: str) -> None:
    _cf("DELETE", f"/zones/{zone_id}/workers/routes/{route_id}", token)


def _create_route(zone_id: str, domain: str, token: str) -> None:
    # Use the most-specific pattern (no leading *) so CF picks ours over
    # any leftover wildcard routes. Pattern: <domain>/* matches both apex
    # and any path under it; the bare apex (no path) is handled by the
    # Custom Domain binding (which we also create/replace below).
    pattern = f"{domain}/*"
    _cf("POST", f"/zones/{zone_id}/workers/routes", token, body={
        "pattern": pattern,
        "script": SCRIPT_NAME,
    })


def _list_custom_domains(zone_id: str, account_id: str, token: str) -> list[dict]:
    """Custom Domains are per-account, filtered by zone. Different API
    surface from Worker Routes — bound at account level via /accounts/{id}/workers/domains."""
    try:
        payload = _cf("GET",
                      f"/accounts/{account_id}/workers/domains?zone_id={zone_id}",
                      token)
        return payload.get("result", []) or []
    except CFError:
        # If our token lacks the account-level custom-domains scope, return
        # empty — the per-zone Worker Routes binding still works at lower
        # priority. Honest degradation.
        return []


def _delete_custom_domain(domain_id: str, account_id: str, token: str) -> None:
    _cf("DELETE",
        f"/accounts/{account_id}/workers/domains/{domain_id}",
        token)


def _attach_custom_domain(hostname: str, zone_id: str, account_id: str,
                          token: str) -> None:
    """Attach our Worker as the Custom Domain owner for this hostname.
    Custom Domains are the highest-priority Worker binding (above Routes),
    so this is the cleanest way to guarantee our redirect Worker fires."""
    _cf("PUT",
        f"/accounts/{account_id}/workers/domains",
        token,
        body={
            "environment": "production",
            "hostname": hostname,
            "service": SCRIPT_NAME,
            "zone_id": zone_id,
        })


def bind_zone(domain: str, account_id: str, token: str,
              reassign: bool = True) -> str:
    """Bind our Worker to the zone, REPLACING any existing Worker bindings.

    GammaQC's 923-zone allocation (1333xxx) was previously bound to
    BlendRoastGrind + NoirLynX-sovereign-edge Workers. Per Commander
    2026-06-09: 'any xyz can be reassigned'. So this script DELETES all
    existing Worker Routes + Custom Domains on each zone before installing
    ours.

    DrkLynX's allocation (1334xxx + 1411xxx) is EXCLUDED by the partition
    in mesh/data/domains-gammaqc.csv — those zones are never touched.

    Returns: 'ok' | 'no zone' | 'error: ...'
    """
    zone_id = _get_zone_id(domain, token)
    if not zone_id:
        return "no zone"

    if reassign:
        # 1. Delete Custom Domain bindings for this zone (highest priority,
        #    must clear first or our Route gets shadowed).
        for cd in _list_custom_domains(zone_id, account_id, token):
            _delete_custom_domain(cd["id"], account_id, token)
            time.sleep(SLEEP_S)
        # 2. Delete ALL Worker Routes on this zone (we're reassigning).
        for r in _list_routes(zone_id, token):
            _delete_route(zone_id, r["id"], token)
            time.sleep(SLEEP_S)

    # 3. Install our Route (path-wildcard, most-specific pattern).
    _create_route(zone_id, domain, token)
    time.sleep(SLEEP_S)
    # 4. Install our Custom Domain (highest-priority binding — guarantees
    #    no future Route addition can shadow our redirect).
    try:
        _attach_custom_domain(domain, zone_id, account_id, token)
    except CFError as e:
        # Custom Domain attach can fail if token lacks the scope OR if
        # hostname is in use by another zone/service. Route alone is
        # still functional; degrade gracefully.
        msg = str(e)[:120]
        return f"ok (route only — custom domain attach failed: {msg})"
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path,
                    help="path to GammaQC allocation CSV (mesh/data/domains-gammaqc.csv)")
    ap.add_argument("--account-id", default=None,
                    help=f"Cloudflare account ID (default: env CF_ACCOUNT_ID or {DEFAULT_ACCOUNT_ID})")
    ap.add_argument("--skip-upload", action="store_true",
                    help="skip the worker script upload (use if script unchanged + just re-binding)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap how many zones to bind (0=all); use --limit 3 for smoke test")
    ap.add_argument("--start", type=int, default=0,
                    help="skip first N zones (for resuming partial runs)")
    args = ap.parse_args()

    token = os.environ.get("CF_API_TOKEN")
    if not token:
        print("::error::CF_API_TOKEN env var not set", file=sys.stderr)
        sys.exit(1)
    account_id = args.account_id or os.environ.get("CF_ACCOUNT_ID") or DEFAULT_ACCOUNT_ID

    if not SCRIPT_PATH.exists():
        print(f"::error::Worker script not found at {SCRIPT_PATH}", file=sys.stderr)
        sys.exit(1)
    script_text = SCRIPT_PATH.read_text(encoding="utf-8")
    print(f"[deploy] script: {SCRIPT_PATH.name} ({len(script_text)} bytes)")

    if not args.skip_upload:
        upload_worker_script(token, account_id, script_text)
        time.sleep(SLEEP_S)

    # Read domain list
    domains: list[str] = []
    with args.csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("Domain Name") or "").strip().lower()
            if d:
                domains.append(d)
    if args.start:
        domains = domains[args.start:]
    if args.limit:
        domains = domains[:args.limit]

    est_s = len(domains) * 3 * SLEEP_S
    print(f"[deploy] binding {len(domains)} zone(s) at {RATE_LIMIT_RPS} req/sec "
          f"(~{est_s/60:.1f} min)")

    succeeded = 0
    no_zone: list[str] = []
    failed: list[tuple[str, str]] = []
    for i, d in enumerate(domains, 1):
        try:
            status = bind_zone(d, account_id, token, reassign=True)
        except CFError as e:
            status = f"error: {str(e)[:200]}"
        except Exception as e:
            status = f"error: {type(e).__name__}: {str(e)[:200]}"
        if status.startswith("ok"):
            succeeded += 1
            print(f"[{i:4d}/{len(domains)}] {d}: {status}")
        elif status == "no zone":
            no_zone.append(d)
            print(f"[{i:4d}/{len(domains)}] {d}: NO ZONE")
        else:
            failed.append((d, status))
            print(f"[{i:4d}/{len(domains)}] {d}: {status}")
        if i < len(domains):
            time.sleep(SLEEP_S)

    print()
    print(f"[deploy] DONE")
    print(f"  succeeded: {succeeded}")
    print(f"  no zone  : {len(no_zone)}")
    print(f"  failed   : {len(failed)}")
    if no_zone:
        Path("cf-deploy-worker.no-zone.txt").write_text("\n".join(no_zone) + "\n")
    if failed:
        out = "cf-deploy-worker.failed.txt"
        Path(out).write_text("Domain Name\n" + "\n".join(d for d, _ in failed) + "\n")
        print(f"  failed list -> {out}; resume with: --csv {out} --skip-upload")
        sys.exit(2)


if __name__ == "__main__":
    main()
