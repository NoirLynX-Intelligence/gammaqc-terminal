"""gamma verify — offline SRF audit_receipt verifier tests.

Covers:
  - Canonical bytes match Python backend + JS Worker
  - inputs_hash check (matches request body / mismatches)
  - Signature verification with a known-good Ed25519 keypair
  - Signature mismatch detection (tampered receipt)
  - Receipt without attestor surfaces as "unverifiable"
  - Malformed receipts handled gracefully
  - Top-level API response wrapper (audit_receipt nested under response)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from gammaqc_terminal.verify import (
    canonical_bytes,
    verify_receipt,
    _extract_pubkey_bytes,
)


# Generate a known keypair for deterministic test fixtures
def _gen_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, PublicFormat, NoEncryption,
    )
    p = Ed25519PrivateKey.generate()
    priv_raw = p.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = p.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return p, priv_raw, pub_raw


def _b64url(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _sign(priv, receipt_core: dict) -> str:
    msg = canonical_bytes(receipt_core)
    return priv.sign(msg).hex()


def _make_receipt(priv, pub, kid: str = "srf-test", original_request: dict = None):
    """Build a fully signed receipt suitable for verification."""
    req = original_request if original_request is not None else {"event_class": "fed_hike", "rows": []}
    inputs_hash = "sha256:" + hashlib.sha256(canonical_bytes(req)).hexdigest()
    core = {
        "version": "srf-receipt-v0",
        "artifact_kind": "shock_hedge",
        "issued_at": "2026-06-10T01:42:00Z",
        "commit_sha": "4294555",
        "inputs_hash": inputs_hash,
        "primary_attestation": {"sig": "hmac1", "algo": "HMAC-SHA256", "key_id": "deadbeef"},
        "sps_verdict": None,
        "attestor": {
            "key_id": kid,
            "alg": "ed25519",
            "version": "srf-receipt-v0",
            "signed_at": "2026-06-10T01:42:01Z",
        },
    }
    sig_hex = _sign(priv, core)
    receipt = {**core}
    receipt["attestor"] = {**core["attestor"], "signature": sig_hex}
    receipt["degraded"] = []
    receipt["verifier_hint"] = "..."
    return receipt, req


def _make_jwks(pub: bytes, kid: str = "srf-test"):
    return {
        "keys": [{
            "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA",
            "kid": kid, "use": "sig", "x": _b64url(pub),
        }],
        "rotation_policy": {"period_days": 180, "overlap_days": 60},
    }


@pytest.fixture
def fake_jwks_fetcher(monkeypatch):
    """Patch _fetch_jwks to return whatever the test sets via .jwks attr."""
    holder = {"jwks": None}
    def _fake(url, timeout=5.0):
        if holder["jwks"] is None:
            raise RuntimeError("no jwks set in test fixture")
        return holder["jwks"]
    monkeypatch.setattr("gammaqc_terminal.verify._fetch_jwks", _fake)
    return holder


# ─── Canonical bytes ─────────────────────────────────────────────────────────

def test_canonical_bytes_key_order_independent():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert canonical_bytes(a) == canonical_bytes(b)


def test_canonical_bytes_matches_python_backend_format():
    """Worker's canonicalize(): '{"a":[3,2,1],"b":1,"c":"hi"}'"""
    assert canonical_bytes({"b": 1, "a": [3, 2, 1], "c": "hi"}) == b'{"a":[3,2,1],"b":1,"c":"hi"}'


def test_canonical_bytes_nested_deep_sort():
    assert canonical_bytes({"z": {"y": 1, "x": 2}}) == b'{"z":{"x":2,"y":1}}'


# ─── JWKS extraction ─────────────────────────────────────────────────────────

def test_extract_pubkey_by_kid():
    pub = bytes(range(32))
    jwks = _make_jwks(pub, kid="srf-test")
    extracted = _extract_pubkey_bytes(jwks, "srf-test")
    assert extracted == pub


def test_extract_pubkey_kid_missing_falls_back_to_first():
    pub = bytes(range(32))
    jwks = _make_jwks(pub, kid="srf-test")
    # Asking for unknown kid → falls back to first
    extracted = _extract_pubkey_bytes(jwks, "unknown-kid")
    assert extracted == pub


def test_extract_pubkey_empty_jwks_returns_none():
    assert _extract_pubkey_bytes({"keys": []}, "any") is None


# ─── Receipt verification — happy path ──────────────────────────────────────

def test_verify_valid_receipt_with_request_body(fake_jwks_fetcher):
    priv, _, pub = _gen_keypair()
    receipt, req = _make_receipt(priv, pub)
    fake_jwks_fetcher["jwks"] = _make_jwks(pub)
    verdict = verify_receipt(receipt=receipt, original_request=req)
    assert verdict["valid"] is True
    assert verdict["checks"]["inputs_hash:matches_request"] is True
    assert verdict["checks"]["attestor:sig_valid"] is True
    assert verdict["signature_alg"] == "ed25519"


def test_verify_valid_receipt_without_request_body(fake_jwks_fetcher):
    """Skipping --request-body: signature still verifies, inputs_hash skipped."""
    priv, _, pub = _gen_keypair()
    receipt, _ = _make_receipt(priv, pub)
    fake_jwks_fetcher["jwks"] = _make_jwks(pub)
    verdict = verify_receipt(receipt=receipt, original_request=None)
    assert verdict["valid"] is True
    assert verdict["checks"]["inputs_hash:matches_request"] is None
    assert verdict["checks"]["attestor:sig_valid"] is True


# ─── Tampering detection ────────────────────────────────────────────────────

def test_tampered_artifact_kind_fails(fake_jwks_fetcher):
    """Mutate any signed field → signature invalid."""
    priv, _, pub = _gen_keypair()
    receipt, _ = _make_receipt(priv, pub)
    fake_jwks_fetcher["jwks"] = _make_jwks(pub)
    receipt["artifact_kind"] = "fake_kind"   # tamper
    verdict = verify_receipt(receipt=receipt, original_request=None)
    assert verdict["valid"] is False
    assert verdict["checks"]["attestor:sig_valid"] is False


def test_inputs_hash_mismatch_fails(fake_jwks_fetcher):
    priv, _, pub = _gen_keypair()
    receipt, req = _make_receipt(priv, pub)
    fake_jwks_fetcher["jwks"] = _make_jwks(pub)
    # User passes a DIFFERENT request body than was signed
    different_req = {"event_class": "cpi_hot", "rows": []}
    verdict = verify_receipt(receipt=receipt, original_request=different_req)
    assert verdict["valid"] is False
    assert verdict["checks"]["inputs_hash:matches_request"] is False


def test_swapped_signature_fails(fake_jwks_fetcher):
    """Attacker signs receipt A with their own key, swaps in our kid."""
    priv_us, _, pub_us = _gen_keypair()
    priv_attacker, _, _ = _gen_keypair()
    receipt, _ = _make_receipt(priv_us, pub_us)
    fake_jwks_fetcher["jwks"] = _make_jwks(pub_us)
    # Replace signature with one from attacker over same core
    core = {k: v for k, v in receipt.items() if k not in ("degraded", "verifier_hint")}
    core["attestor"] = {k: v for k, v in core["attestor"].items() if k != "signature"}
    attacker_sig = priv_attacker.sign(canonical_bytes(core)).hex()
    receipt["attestor"]["signature"] = attacker_sig
    verdict = verify_receipt(receipt=receipt, original_request=None)
    assert verdict["valid"] is False


# ─── Degraded receipts (no attestor) ────────────────────────────────────────

def test_receipt_without_attestor_is_unverifiable():
    """Receipt issued in degraded mode (attestor was down) → valid=True
    but signature_present=False → CLI maps to exit code 2."""
    receipt = {
        "version": "srf-receipt-v0",
        "artifact_kind": "shock_hedge",
        "issued_at": "2026-06-10T01:42:00Z",
        "inputs_hash": "sha256:abc",
        "attestor": None,
        "degraded": ["attestor:unavailable"],
    }
    verdict = verify_receipt(receipt=receipt)
    assert verdict["valid"] is True   # no failed check; just nothing to verify
    assert verdict["checks"]["attestor:signature_present"] is False


def test_malformed_receipt_caught_gracefully():
    """Non-dict input handled without raising."""
    verdict = verify_receipt(receipt=["not", "a", "dict"])
    assert verdict["valid"] is False


def test_missing_required_fields_caught():
    receipt = {"artifact_kind": "x"}   # missing version, issued_at, inputs_hash
    verdict = verify_receipt(receipt=receipt)
    assert verdict["valid"] is False
    assert verdict["checks"]["has:version"] is False
    assert verdict["checks"]["has:inputs_hash"] is False


# ─── Cross-language canonical-bytes invariant ────────────────────────────────

def test_canonical_bytes_unicode_escaping():
    """Confirms CLI canonical bytes match the backend/Worker output for
    edge cases the SRF cross-check harness exercises."""
    sample = {"ticker": "NVDA", "label": "Auto-provisioned (Pro)",
              "note": 'embedded "quote" + \\ backslash'}
    out = canonical_bytes(sample)
    expected = (b'{"label":"Auto-provisioned (Pro)",'
                b'"note":"embedded \\"quote\\" + \\\\ backslash",'
                b'"ticker":"NVDA"}')
    assert out == expected
