"""Tests for the Pro-tier hedge surface (gammaqc_terminal.hedge).

We DON'T hit a real backend in CI. Tests cover:
  - request_hedges raises HedgeError without an API key (auth contract)
  - HedgeResponse.error path on 402 (Pro wall surfaces correctly, no crash)
  - HedgeError on 401 / 429 / 5xx (each maps to a distinct user-facing msg)
  - Malformed hedge rows are skipped (no whole-report 500)
  - Round-trip of well-formed response → HedgeRecommendation objects
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import httpx
import pytest

from gammaqc_terminal.config import Config
from gammaqc_terminal.hedge import (
    HedgeError, HedgeRecommendation, HedgeResponse, request_hedges,
)
from gammaqc_terminal.shock import ShockReport, ShockRow


def _make_report() -> ShockReport:
    rows = [
        ShockRow(ticker="NVDA", position_value=10000, asset_class="growth_tech",
                 beta=-0.9, blast_radius=-9000),
        ShockRow(ticker="JPM", position_value=8000, asset_class="financials",
                 beta=0.6, blast_radius=4800),
    ]
    return ShockReport(
        event_text="Fed raises rates 50bps",
        event_class="fed_hike", rows=rows,
        total_position=18000, net_blast_radius=-4200, warnings=[],
    )


def test_request_hedges_raises_without_api_key():
    cfg = Config(api_key=None)
    with pytest.raises(HedgeError, match="no api key"):
        request_hedges(_make_report(), cfg)


def _patch_client_with(response: MagicMock):
    """Helper: monkey-patch httpx.Client used inside hedge._client."""
    class _StubCtx:
        def __enter__(self_inner):
            client = MagicMock()
            client.post = MagicMock(return_value=response)
            return client
        def __exit__(self_inner, *a):
            return False
    return patch("gammaqc_terminal.hedge._client",
                 lambda cfg, timeout=20.0: _StubCtx())


def _mock_response(status_code: int, body=None):
    r = MagicMock()
    r.status_code = status_code
    r.text = json.dumps(body) if body is not None else "{}"
    r.json = MagicMock(return_value=body if body is not None else {})
    return r


def test_request_hedges_402_returns_pro_required(monkeypatch):
    """Pro wall (402) becomes a structured response — NOT an exception."""
    cfg = Config(api_key="sk-test")
    response = _mock_response(402, {"detail": {"error": "pro_tier_required",
                                                "current_tier": "free",
                                                "upgrade_url": "https://gammaqc.com/pro"}})
    with _patch_client_with(response):
        resp = request_hedges(_make_report(), cfg)
    assert resp.error and "pro_tier_required" in resp.error
    assert "free" in resp.error
    assert resp.hedges == []


def test_request_hedges_401_raises():
    cfg = Config(api_key="sk-bad")
    response = _mock_response(401, {"detail": "invalid"})
    with _patch_client_with(response):
        with pytest.raises(HedgeError, match="api key rejected"):
            request_hedges(_make_report(), cfg)


def test_request_hedges_429_raises():
    cfg = Config(api_key="sk-test")
    response = _mock_response(429)
    with _patch_client_with(response):
        with pytest.raises(HedgeError, match="rate-limited"):
            request_hedges(_make_report(), cfg)


def test_request_hedges_5xx_raises():
    cfg = Config(api_key="sk-test")
    response = _mock_response(503)
    with _patch_client_with(response):
        with pytest.raises(HedgeError, match="503"):
            request_hedges(_make_report(), cfg)


def test_request_hedges_network_error_raises():
    cfg = Config(api_key="sk-test")
    class _BlowupCtx:
        def __enter__(self_inner):
            class _ErrClient:
                def post(self_, *a, **kw):
                    raise httpx.ConnectError("connection refused")
            return _ErrClient()
        def __exit__(self_inner, *a):
            return False
    with patch("gammaqc_terminal.hedge._client",
               lambda cfg, timeout=20.0: _BlowupCtx()):
        with pytest.raises(HedgeError, match="backend unreachable"):
            request_hedges(_make_report(), cfg)


def test_request_hedges_non_json_response_raises():
    cfg = Config(api_key="sk-test")
    r = MagicMock()
    r.status_code = 200
    r.text = "<html>not json</html>"
    r.json = MagicMock(side_effect=ValueError("bad json"))
    with _patch_client_with(r):
        with pytest.raises(HedgeError, match="non-JSON"):
            request_hedges(_make_report(), cfg)


def test_request_hedges_well_formed_response_parses():
    cfg = Config(api_key="sk-test")
    payload = {
        "ok": True, "event_class": "fed_hike",
        "hedges": [
            {"ticker": "NVDA", "position_value": 10000, "blast_radius": -9000,
             "hedge_action": "SHORT_HEDGE", "instrument": "QQQ puts",
             "sizing_pct_of_position": 25, "hedge_notional": 2500.0,
             "rationale": "Standard growth-tech protection.",
             "disclaimer": "Heuristic hedge template — not personalized advice."},
        ],
        "unhedgeable": [{"ticker": "ZZZZZ", "asset_class": "unknown",
                         "reason": "no template"}],
        "total_hedge_notional": 2500.0,
        "attestation_hash": "abc123def" + "0" * 56,
    }
    response = _mock_response(200, payload)
    with _patch_client_with(response):
        resp = request_hedges(_make_report(), cfg)
    assert resp.error is None
    assert resp.event_class == "fed_hike"
    assert len(resp.hedges) == 1
    assert resp.hedges[0].ticker == "NVDA"
    assert resp.hedges[0].hedge_notional == 2500.0
    assert resp.hedges[0].sizing_pct_of_position == 25
    assert resp.total_hedge_notional == 2500.0
    assert resp.attestation_hash.startswith("abc123def")
    assert len(resp.unhedgeable) == 1


def test_request_hedges_skips_malformed_rows():
    """A malformed hedge row in the response must NOT crash the whole call.
    Defensive — backend could legitimately ship an edge case (e.g. missing
    field on one row) and the user should still get the other hedges."""
    cfg = Config(api_key="sk-test")
    payload = {
        "event_class": "fed_hike",
        "hedges": [
            {"ticker": "NVDA", "position_value": 10000, "blast_radius": -9000,
             "hedge_action": "SHORT_HEDGE", "instrument": "QQQ puts",
             "sizing_pct_of_position": 25, "hedge_notional": 2500.0,
             "rationale": "ok", "disclaimer": "test"},
            {"malformed": "missing all required fields"},   # should be skipped
            {"ticker": "AAPL", "position_value": "not_a_number",   # bad type → skipped
             "hedge_action": "X", "instrument": "Y",
             "sizing_pct_of_position": 0, "hedge_notional": 0,
             "rationale": "", "disclaimer": ""},
        ],
        "unhedgeable": [],
        "total_hedge_notional": 2500.0,
    }
    response = _mock_response(200, payload)
    with _patch_client_with(response):
        resp = request_hedges(_make_report(), cfg)
    assert resp.error is None
    assert len(resp.hedges) == 1   # only the valid one
    assert resp.hedges[0].ticker == "NVDA"


def test_request_hedges_payload_shape_matches_backend_contract():
    """Verify the JSON body we POST is the EXACT shape the backend's
    ShockHedgeReq pydantic model expects (event_class + rows with
    ticker/asset_class/beta/blast_radius/position_value)."""
    cfg = Config(api_key="sk-test")
    captured = {}
    class _SpyCtx:
        def __enter__(self_inner):
            class _SpyClient:
                def post(self_, path, json=None):
                    captured["path"] = path
                    captured["json"] = json
                    return _mock_response(200, {"hedges": [], "unhedgeable": [],
                                                  "event_class": "fed_hike",
                                                  "total_hedge_notional": 0})
            return _SpyClient()
        def __exit__(self_inner, *a):
            return False
    with patch("gammaqc_terminal.hedge._client",
               lambda cfg, timeout=20.0: _SpyCtx()):
        request_hedges(_make_report(), cfg)

    assert captured["path"] == "/api/oracle/shock/hedge"
    assert "event_class" in captured["json"]
    assert "rows" in captured["json"]
    sample_row = captured["json"]["rows"][0]
    assert set(sample_row.keys()) == {
        "ticker", "asset_class", "beta", "blast_radius", "position_value",
    }
