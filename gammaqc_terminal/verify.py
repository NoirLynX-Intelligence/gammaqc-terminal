"""gamma verify — offline Sovereign Review Fabric receipt verifier.

A FREE-TIER command. Takes a receipt JSON (from any Gamma QC artifact
response) and verifies it offline using:
  1. inputs_hash sanity (re-canonicalize + sha256 the original request,
     compare to receipt.inputs_hash)
  2. Attestor signature (fetch public JWKS from
     https://attest.gammaqc.com/.well-known/attestor.json, verify
     Ed25519 signature over receipt-core canonical bytes)

Why offline-first: an allocator doing due diligence shouldn't have to
trust our backend to verify our backend's claims. Public-key crypto
+ a fetch-once JWKS means the verifier needs only the receipt + the
public key. No server roundtrip, no API key, no rate limit.

Usage:
  gamma verify receipt.json
  gamma verify receipt.json --request-body original_request.json
  gamma verify - < receipt.json
  cat receipt.json | gamma verify
  gamma verify --jwks-url https://attest.gammaqc.com/.well-known/attestor.json receipt.json

Exit codes:
  0  receipt valid (all available checks pass)
  1  receipt invalid (signature mismatch or hash mismatch)
  2  receipt unverifiable (degraded — missing attestor, no signature)
  3  malformed input
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

DEFAULT_JWKS_URL = "https://attest.gammaqc.com/.well-known/attestor.json"


def canonical_bytes(obj: Any) -> bytes:
    """MUST match backend/_src/services/srf_receipt.py::_canonical_bytes
    AND mesh/workers/srf-attestor/worker.js::canonicalize() byte-for-byte.

    If you change this, change the other two. Cross-language smoke test
    at backend/_tools/srf_canonical_*.{py,js}."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load_json(path_or_dash: str) -> Any:
    if path_or_dash == "-":
        return json.load(sys.stdin)
    p = Path(path_or_dash)
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {path_or_dash}")
    return json.loads(p.read_text(encoding="utf-8"))


def _fetch_jwks(url: str, timeout: float = 5.0) -> dict:
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def _extract_pubkey_bytes(jwks: dict, key_id: Optional[str]) -> Optional[bytes]:
    """Find the public key matching the kid, decode the base64url x field
    to raw 32 bytes for Ed25519."""
    import base64
    keys = jwks.get("keys", [])
    target = None
    if key_id:
        for k in keys:
            if k.get("kid") == key_id:
                target = k
                break
    if target is None and keys:
        target = keys[0]   # fall back to first key
    if target is None:
        return None
    x = target.get("x", "")
    # base64url decode (add padding)
    pad = (-len(x)) % 4
    return base64.urlsafe_b64decode(x + ("=" * pad))


def _verify_ed25519(pub: bytes, sig: bytes, msg: bytes) -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
        pk = Ed25519PublicKey.from_public_bytes(pub)
        try:
            pk.verify(sig, msg)
            return True
        except InvalidSignature:
            return False
    except ImportError:
        # `cryptography` is in our deps; this should never happen.
        return False


def verify_receipt(
    receipt: dict,
    original_request: Optional[Any] = None,
    jwks_url: str = DEFAULT_JWKS_URL,
) -> dict:
    """Returns a verdict dict with shape:
      {
        "valid":           bool,
        "checks":          {check_name: True/False/None},
        "reasons":         [str, ...],   # human-readable per-check notes
        "signature_alg":   str | None,
        "key_id":          str | None,
        "degraded_subsystems": [str, ...],
      }
    """
    checks = {}
    reasons = []
    valid = True

    if not isinstance(receipt, dict):
        return {"valid": False, "checks": {}, "reasons": ["receipt is not a JSON object"]}

    # 1. Structural sanity
    for required in ("version", "artifact_kind", "issued_at", "inputs_hash"):
        if required not in receipt:
            checks[f"has:{required}"] = False
            reasons.append(f"receipt missing required field: {required}")
            valid = False
        else:
            checks[f"has:{required}"] = True

    # 2. Surface degraded subsystems honestly
    degraded = list(receipt.get("degraded") or [])
    if degraded:
        reasons.append("receipt was produced in degraded mode: " + ", ".join(degraded))

    # 3. inputs_hash check (only if user supplied original request)
    if original_request is not None:
        actual_hash = "sha256:" + hashlib.sha256(canonical_bytes(original_request)).hexdigest()
        claimed = receipt.get("inputs_hash")
        match = (actual_hash == claimed)
        checks["inputs_hash:matches_request"] = match
        if not match:
            valid = False
            reasons.append(f"inputs_hash mismatch — claimed {claimed}, computed {actual_hash}")
        else:
            reasons.append("✓ inputs_hash matches the request body you provided")
    else:
        checks["inputs_hash:matches_request"] = None
        reasons.append("(skipped inputs_hash check — pass --request-body to verify)")

    # 4. Attestor signature check
    attestor = receipt.get("attestor")
    if not attestor:
        checks["attestor:signature_present"] = False
        reasons.append("⚠ no attestor signature in receipt (attestor was unavailable at issue time)")
    else:
        checks["attestor:signature_present"] = True
        sig_hex = attestor.get("signature", "")
        alg = attestor.get("alg", "ed25519")
        key_id = attestor.get("key_id")

        if alg != "ed25519":
            checks["attestor:alg_supported"] = False
            reasons.append(f"unsupported attestor alg: {alg} (this verifier supports ed25519 only)")
            valid = False
        else:
            try:
                jwks = _fetch_jwks(jwks_url)
            except Exception as e:
                checks["attestor:jwks_fetched"] = False
                reasons.append(f"could not fetch attestor JWKS from {jwks_url}: {e}")
                valid = False
            else:
                checks["attestor:jwks_fetched"] = True
                pub = _extract_pubkey_bytes(jwks, key_id)
                if not pub:
                    checks["attestor:pubkey_found"] = False
                    reasons.append(f"no matching public key in JWKS for kid={key_id}")
                    valid = False
                else:
                    checks["attestor:pubkey_found"] = True
                    # Reconstruct the receipt-core that was signed.
                    # The Worker's /sign endpoint adds an `attestor` field
                    # to the receipt BEFORE signing — so the signed bytes
                    # include attestor.key_id/version/signed_at but NOT
                    # the signature itself or the verifier_hint/degraded.
                    core = {
                        k: v for k, v in receipt.items()
                        if k not in ("degraded", "verifier_hint")
                    }
                    # Replace `attestor` with its as-signed form (no `signature` field)
                    if "attestor" in core and isinstance(core["attestor"], dict):
                        core["attestor"] = {
                            k: v for k, v in core["attestor"].items()
                            if k != "signature"
                        }
                    msg_bytes = canonical_bytes(core)
                    try:
                        sig_bytes = bytes.fromhex(sig_hex)
                    except ValueError:
                        checks["attestor:sig_decoded"] = False
                        reasons.append("attestor signature is not valid hex")
                        valid = False
                    else:
                        if _verify_ed25519(pub, sig_bytes, msg_bytes):
                            checks["attestor:sig_valid"] = True
                            reasons.append(f"✓ attestor signature valid (kid={key_id})")
                        else:
                            checks["attestor:sig_valid"] = False
                            reasons.append(f"✗ attestor signature INVALID for kid={key_id}")
                            valid = False

    return {
        "valid": valid,
        "checks": checks,
        "reasons": reasons,
        "signature_alg": (attestor or {}).get("alg") if attestor else None,
        "key_id": (attestor or {}).get("key_id") if attestor else None,
        "degraded_subsystems": degraded,
    }
