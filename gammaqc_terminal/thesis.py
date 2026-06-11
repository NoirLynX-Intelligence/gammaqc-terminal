"""gamma thesis * — Sealed Ledger CLI commands.

Power-user shortcuts to the same Sealed Ledger surface the web app
shows at /collision, /ledger, /ledger/new. Identical backend, same
PQC-sealed receipts, same ARCC verdicts. The CLI exists for traders
who think in shells and need to script: pre-market checks before
their broker even opens.

Commands:
  gamma thesis new        — mint a thesis (prompts or flags)
  gamma thesis list       — list active theses
  gamma thesis show <id>  — single thesis detail + history
  gamma thesis recheck    — on-demand ARCC re-audit
  gamma collision         — today's morning collision matrix

All require a Pro API key (`gamma login --api-key gqc_live_xxx`).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from .auth import _client, AuthError, require_pro
from .config import Config


def _err(msg: str) -> str:
    return f"[red]✗[/] {msg}"


def mint_thesis(cfg: Config, *, ticker: str, side: str,
                 entry_price: float, stop_price: float, target_price: float,
                 capital_allocation_usd: float,
                 rationale_text: str) -> Dict[str, Any]:
    """POST /api/oracle/thesis. Returns the full response dict."""
    require_pro(cfg)
    with _client(cfg, timeout=90.0) as c:
        r = c.post("/api/oracle/thesis", json={
            "ticker": ticker.upper(),
            "side": side,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "capital_allocation_usd": capital_allocation_usd,
            "rationale_text": rationale_text,
        })
    if r.status_code == 401:
        raise AuthError("session_expired — run `gamma login --api-key`")
    if r.status_code == 502:
        raise RuntimeError("quote_unavailable — Yahoo/AlphaVantage rate-limit; retry 30s")
    if r.status_code == 503:
        raise RuntimeError(f"arcc_unavailable — backend says {r.text[:120]}")
    if r.status_code != 200:
        raise RuntimeError(f"mint_failed status={r.status_code}: {r.text[:200]}")
    return r.json()


def list_active(cfg: Config) -> List[Dict[str, Any]]:
    require_pro(cfg)
    with _client(cfg, timeout=15.0) as c:
        r = c.get("/api/oracle/theses")
    if r.status_code != 200:
        raise RuntimeError(f"list_failed status={r.status_code}: {r.text[:200]}")
    return r.json().get("active", [])


def get_thesis_detail(cfg: Config, thesis_id: str) -> Dict[str, Any]:
    require_pro(cfg)
    with _client(cfg, timeout=15.0) as c:
        r = c.get(f"/api/oracle/thesis/{thesis_id}")
    if r.status_code == 404:
        raise RuntimeError("thesis_not_found")
    if r.status_code != 200:
        raise RuntimeError(f"fetch_failed status={r.status_code}")
    return r.json()


def recheck_thesis(cfg: Config, thesis_id: str) -> Dict[str, Any]:
    require_pro(cfg)
    with _client(cfg, timeout=90.0) as c:
        r = c.post(f"/api/oracle/thesis/{thesis_id}/recheck", json={})
    if r.status_code != 200:
        raise RuntimeError(f"recheck_failed status={r.status_code}: {r.text[:200]}")
    return r.json()


def collision_today(cfg: Config) -> Dict[str, Any]:
    require_pro(cfg)
    with _client(cfg, timeout=15.0) as c:
        r = c.get("/api/oracle/collision/today")
    if r.status_code != 200:
        raise RuntimeError(f"matrix_failed status={r.status_code}")
    return r.json()


def log_decision(cfg: Config, thesis_id: str, *,
                  decision: str, reason_text: str = "",
                  closed_price: Optional[float] = None) -> Dict[str, Any]:
    require_pro(cfg)
    payload = {"decision": decision, "reason_text": reason_text}
    if closed_price is not None:
        payload["closed_price"] = closed_price
    with _client(cfg, timeout=20.0) as c:
        r = c.post(f"/api/oracle/thesis/{thesis_id}/decision", json=payload)
    if r.status_code == 400:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        raise RuntimeError(body.get("detail", {}).get("hint") or "bad_request")
    if r.status_code != 200:
        raise RuntimeError(f"decision_failed status={r.status_code}: {r.text[:200]}")
    return r.json()
