#!/usr/bin/env python3
"""extract-gammaqc-domains.py — enforce the GammaQC vs DrkLynX inventory partition.

Reads one or more NameCheap-export CSVs and writes a SINGLE GammaQC-only
domain list. The partition rule is HARD-CODED — wrong-side-of-the-line
domains can never be auto-included by accident, even on operator typo.

Partition (Commander allocation 2026-06-09):
    GammaQC : 1333xxx.xyz only        (capped at GAMMAQC_MAX = 1000)
    DrkLynX : 1334xxx, 1411xxx, named (everything else — left alone)

Why hard-code: the bulk-DNS script that uses this list will mass-flip
A-records to point at the GammaQC Hostinger box. If we accidentally
included a DrkLynX domain, we'd silently steal it from DrkLynX edge
infrastructure. Hard-coding the partition rule + cap means the only way
to allocate a DrkLynX domain to GammaQC is to consciously edit this
script (which leaves a git diff + commit signature).

Usage:
    python3 extract-gammaqc-domains.py \\
        --input "C:/Users/Joel/Downloads/Domain_List (8).csv" \\
        --input "C:/Users/Joel/Downloads/Domain_List (9).csv" \\
        --output /opt/gammaqc-mesh/data/domains-gammaqc.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# ─── Partition rules ─────────────────────────────────────────────────────────
#
# A domain is allocated to GammaQC iff it matches GAMMAQC_PATTERN AND we
# haven't yet hit GAMMAQC_MAX. Everything else (1334xxx, 1411xxx, named,
# any future range) stays unclaimed → DrkLynX or other use.
#
# IF YOU EDIT THESE: leave a comment explaining the allocation change so
# the git blame trail makes the boundary movement obvious.

GAMMAQC_PATTERN = re.compile(r"^1333\d{3}\.xyz$", re.IGNORECASE)
GAMMAQC_MAX = 1000   # Commander's cap as of 2026-06-09

# Defensive: never auto-claim these even if they accidentally match the pattern.
# Add domains here if a 1333xxx ever needs to be carved out for non-GammaQC use.
GAMMAQC_DENYLIST: set[str] = set()


def _read_csv_domains(path: Path) -> list[str]:
    domains: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "Domain Name" not in (reader.fieldnames or []):
            raise SystemExit(f"::error::{path}: missing 'Domain Name' column "
                             f"(got {reader.fieldnames})")
        for row in reader:
            d = (row.get("Domain Name") or "").strip().lower()
            if d:
                domains.append(d)
    return domains


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", required=True, type=Path,
                    help="path to a NameCheap-export CSV (can be given multiple times)")
    ap.add_argument("--output", required=True, type=Path,
                    help="path to write the GammaQC-only CSV")
    ap.add_argument("--dry-run", action="store_true",
                    help="print partition stats but don't write the output file")
    args = ap.parse_args()

    # Read + dedup
    all_domains: list[str] = []
    seen: set[str] = set()
    for p in args.input:
        if not p.exists():
            raise SystemExit(f"::error::input file not found: {p}")
        for d in _read_csv_domains(p):
            if d not in seen:
                seen.add(d)
                all_domains.append(d)
    print(f"[extract] read {len(all_domains)} unique domain(s) across "
          f"{len(args.input)} source file(s)")

    # Partition
    gammaqc: list[str] = []
    drklynx: list[str] = []
    denied: list[str] = []
    for d in sorted(all_domains):   # sort for stable, reproducible output
        if GAMMAQC_PATTERN.match(d):
            if d in GAMMAQC_DENYLIST:
                denied.append(d)
            elif len(gammaqc) < GAMMAQC_MAX:
                gammaqc.append(d)
            else:
                # Hit cap; remaining 1333xxx matches go to overflow (still
                # NOT auto-claimed for DrkLynX — left unallocated until
                # Commander decides).
                drklynx.append(d)
        else:
            drklynx.append(d)

    print()
    print(f"[extract] PARTITION RESULT:")
    print(f"  GammaQC      : {len(gammaqc):4d} domain(s)  (matches 1333xxx.xyz, cap={GAMMAQC_MAX})")
    print(f"  DrkLynX/other: {len(drklynx):4d} domain(s)  (everything else)")
    if denied:
        print(f"  Denylisted   : {len(denied):4d} domain(s)  (in pattern but explicitly excluded)")
    print()
    if gammaqc:
        print(f"  GammaQC range: {gammaqc[0]} … {gammaqc[-1]}")
    if drklynx:
        # Show breakdown of DrkLynX-side by prefix for clarity
        from collections import Counter
        prefixes = Counter()
        for d in drklynx:
            m = re.match(r"^(\d{4})", d)
            if m:
                prefixes[m.group(1) + "xxx"] += 1
            else:
                prefixes["named/other"] += 1
        print(f"  DrkLynX-side breakdown:")
        for prefix, count in sorted(prefixes.items()):
            print(f"    {prefix}: {count}")

    if args.dry_run:
        print()
        print("[extract] --dry-run: not writing output file")
        return

    # Write the GammaQC list in NameCheap CSV format (same shape as input
    # so namecheap-bulk-dns.py can read it directly).
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Domain Name", "Allocated_To", "Allocated_At"])
        for d in gammaqc:
            writer.writerow([d, "GammaQC", "2026-06-09"])

    print()
    print(f"[extract] wrote {len(gammaqc)} GammaQC domain(s) to {args.output}")
    # ASCII-only to survive Windows cp1252 default stdout encoding
    print(f"[extract] OK -- safe to pass to namecheap-bulk-dns.py:")
    print(f"    python3 namecheap-bulk-dns.py --csv {args.output} --ip <hostinger_ip>")


if __name__ == "__main__":
    main()
