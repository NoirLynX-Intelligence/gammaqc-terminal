#!/usr/bin/env python3
"""namecheap-bulk-dns.py — set A-records for every domain in a CSV via NameCheap's API.

Usage:
    python namecheap-bulk-dns.py --csv domains.csv --ip 187.124.95.35

Reads a CSV with a `Domain Name` column (matches NameCheap's export format).
For each domain, sets:
    Type=A, Host=@,   Address=<ip>, TTL=1800
    Type=A, Host=www, Address=<ip>, TTL=1800

NameCheap's API rate-limit (per docs): 50 req/min, 1000 req/sec under burst,
700/day for sandbox. The setHosts call is one request per domain (you set ALL
records for a domain in a single call), so 1,500 domains = 1,500 requests.
At 50/min that's ~30 minutes to complete.

CREDENTIALS — set as env vars (never as CLI args; CLI args leak into shell history):
    NC_API_USER        — your NameCheap username
    NC_API_KEY         — API key from namecheap.com/myaccount → Profile → Tools → API Access
    NC_API_USERNAME    — usually same as NC_API_USER unless using a sub-account
    NC_CLIENT_IP       — the IP THIS SCRIPT runs from (must match whitelist in NC API panel)

PRE-FLIGHT (run from the Hostinger box where IP whitelist matches):
    1. NC → Profile → Tools → API Access → toggle ON
    2. Whitelist the Hostinger box IP (187.124.95.35)
    3. Wait 5 min for whitelist to propagate
    4. ssh root@187.124.95.35
    5. cd /opt/gammaqc-mesh && python3 scripts/namecheap-bulk-dns.py --csv <path> --ip 187.124.95.35

The script is IDEMPOTENT — setHosts replaces all records for the domain in one
atomic call. Re-running just rewrites the same A-records.

EXIT CODES:
    0 — all domains succeeded
    1 — env config missing
    2 — at least one domain failed (full list logged); rerun those individually
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterator, Tuple


NC_API_BASE = "https://api.namecheap.com/xml.response"
NC_API_SANDBOX = "https://api.sandbox.namecheap.com/xml.response"
DEFAULT_TTL = 1800   # 30 minutes — fast enough for fixes, slow enough to spare NC's nameservers
RATE_LIMIT_RPM = 45  # under NC's 50/min ceiling with 10% headroom
SLEEP_BETWEEN_CALLS_S = 60.0 / RATE_LIMIT_RPM


def _env_or_die(*names: str) -> Tuple[str, ...]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print(f"::error::missing env var(s): {missing}", file=sys.stderr)
        print("Set with:", file=sys.stderr)
        for n in missing:
            print(f"  export {n}=<value>", file=sys.stderr)
        sys.exit(1)
    return tuple(os.environ[n] for n in names)


def _split_sld_tld(domain: str) -> Tuple[str, str]:
    """NameCheap API requires SLD + TLD separately for setHosts. E.g.
    '1334077.xyz' → ('1334077', 'xyz'). Doesn't handle .co.uk style TLDs
    (we don't have any in this inventory)."""
    parts = domain.strip().split(".")
    if len(parts) < 2:
        raise ValueError(f"invalid domain: {domain!r}")
    return ".".join(parts[:-1]), parts[-1]


def _read_domains(csv_path: str) -> Iterator[str]:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "Domain Name" not in (reader.fieldnames or []):
            raise SystemExit(f"::error::CSV must have a 'Domain Name' column (got {reader.fieldnames})")
        for row in reader:
            d = (row.get("Domain Name") or "").strip().lower()
            if d:
                yield d


def _build_set_hosts_params(api_user: str, api_key: str, username: str,
                            client_ip: str, sld: str, tld: str,
                            target_ip: str, ttl: int) -> str:
    """NameCheap setHosts: one host per (HostNameN, RecordTypeN, AddressN, TTLN) tuple.
    We set @ and www to the same A-record target."""
    return urllib.parse.urlencode({
        "ApiUser": api_user,
        "ApiKey": api_key,
        "UserName": username,
        "ClientIp": client_ip,
        "Command": "namecheap.domains.dns.setHosts",
        "SLD": sld,
        "TLD": tld,
        "HostName1": "@",   "RecordType1": "A", "Address1": target_ip, "TTL1": ttl,
        "HostName2": "www", "RecordType2": "A", "Address2": target_ip, "TTL2": ttl,
    })


def _call_nc(url: str, params: str, timeout: float = 30.0) -> ET.Element:
    req = urllib.request.Request(
        url, data=params.encode("utf-8"), method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    root = ET.fromstring(body)
    return root


def _parse_nc_response(root: ET.Element) -> Tuple[bool, str]:
    """Returns (ok, message). NC wraps results in <ApiResponse Status="OK|ERROR">."""
    status = root.attrib.get("Status", "UNKNOWN")
    if status == "OK":
        # Look for DomainDNSSetHostsResult IsSuccess
        ns = {"nc": "http://api.namecheap.com/xml.response"}
        result = root.find(".//nc:DomainDNSSetHostsResult", ns)
        if result is not None and result.attrib.get("IsSuccess", "").lower() == "true":
            return True, "ok"
        return False, "API returned OK but DomainDNSSetHostsResult IsSuccess=false"
    # Extract first error
    errs = root.findall(".//{http://api.namecheap.com/xml.response}Error")
    if errs:
        return False, f"NC error: {errs[0].text!r} (code {errs[0].attrib.get('Number')})"
    return False, "unknown NC response"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="path to domain list CSV (NameCheap export format)")
    ap.add_argument("--ip", required=True, help="IPv4 target for A records (e.g. 187.124.95.35)")
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    ap.add_argument("--sandbox", action="store_true", help="use NC sandbox (testing only)")
    ap.add_argument("--limit", type=int, default=0, help="cap how many domains to process (0=all)")
    ap.add_argument("--start", type=int, default=0, help="skip the first N domains (for resuming)")
    args = ap.parse_args()

    api_user, api_key, username, client_ip = _env_or_die(
        "NC_API_USER", "NC_API_KEY", "NC_API_USERNAME", "NC_CLIENT_IP",
    )
    endpoint = NC_API_SANDBOX if args.sandbox else NC_API_BASE

    domains = list(_read_domains(args.csv))
    if args.start:
        domains = domains[args.start:]
    if args.limit:
        domains = domains[:args.limit]
    print(f"[bulk-dns] {len(domains)} domain(s) to process at {RATE_LIMIT_RPM}/min "
          f"(~{len(domains) * SLEEP_BETWEEN_CALLS_S / 60:.1f} min)")
    print(f"[bulk-dns] endpoint = {endpoint}")
    print(f"[bulk-dns] target IP = {args.ip}, TTL = {args.ttl}")

    succeeded = 0
    failed: list[Tuple[str, str]] = []
    for i, domain in enumerate(domains, 1):
        try:
            sld, tld = _split_sld_tld(domain)
        except ValueError as e:
            failed.append((domain, str(e)))
            print(f"[{i:4d}/{len(domains)}] {domain}: SKIP — {e}")
            continue

        params = _build_set_hosts_params(api_user, api_key, username, client_ip,
                                          sld, tld, args.ip, args.ttl)
        try:
            root = _call_nc(endpoint, params)
            ok, msg = _parse_nc_response(root)
        except Exception as e:
            ok, msg = False, f"http error: {type(e).__name__}: {e}"

        if ok:
            succeeded += 1
            print(f"[{i:4d}/{len(domains)}] {domain}: ✓")
        else:
            failed.append((domain, msg))
            print(f"[{i:4d}/{len(domains)}] {domain}: ✗ {msg}")

        # Rate-limit (NC enforces ~50/min). Skip on last iteration.
        if i < len(domains):
            time.sleep(SLEEP_BETWEEN_CALLS_S)

    print()
    print(f"[bulk-dns] DONE — {succeeded} succeeded, {len(failed)} failed out of {len(domains)}")
    if failed:
        print("[bulk-dns] failed domains (first 10):")
        for d, msg in failed[:10]:
            print(f"  - {d}: {msg}")
        if len(failed) > 10:
            print(f"  ... +{len(failed) - 10} more")
        # Write a file with the failed list so the operator can re-run just those
        out = "namecheap-bulk-dns.failed.txt"
        with open(out, "w") as f:
            f.write("Domain Name\n")
            for d, _ in failed:
                f.write(f"{d}\n")
        print(f"[bulk-dns] failed list written to {out}")
        sys.exit(2)


if __name__ == "__main__":
    main()
