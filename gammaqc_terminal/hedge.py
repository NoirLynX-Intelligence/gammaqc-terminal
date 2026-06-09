"""Algorithmic Hedge Strategy — Pro-tier lift over a local Shock Report.

Free-tier behavior: not invoked. `gamma shock` shows the locked CTA.

Pro-tier behavior: when --hedge is passed AND a valid Pro API key is
configured, POSTs the shock rows to /api/oracle/shock/hedge and renders
the algorithmic hedge response inline.

Honest fallback: if the backend is unreachable / returns 402 / returns
malformed data, the CLI surfaces the failure cleanly and the local
Blast Radius Report still renders. We never silently degrade.

Privacy contract:
  - Position TICKERS leave the machine (backend needs them for hedge
    template lookup). Dollar amounts also leave (needed for hedge
    sizing) — this is the Pro boundary the user explicitly authenticated
    past. The free tier's "CSV never leaves" promise holds because
    free-tier callers never get here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .auth import _client
from .config import Config
from .shock import ShockReport


@dataclass
class HedgeRecommendation:
    ticker: str
    position_value: float
    blast_radius: float
    hedge_action: str
    instrument: str
    sizing_pct_of_position: int
    hedge_notional: float
    rationale: str
    disclaimer: str


@dataclass
class HedgeResponse:
    event_class: str
    hedges: list[HedgeRecommendation]
    unhedgeable: list[dict[str, str]]
    total_hedge_notional: float
    attestation_hash: str | None
    error: str | None = None


class HedgeError(Exception):
    """Raised when the backend cannot be reached or returns an error.
    Caller catches and renders the failure gracefully — the local Shock
    Report is still useful even when hedge generation fails."""


def request_hedges(report: ShockReport, cfg: Config) -> HedgeResponse:
    """POST the shock report to /api/oracle/shock/hedge. Raises HedgeError
    on network/parse failure; returns HedgeResponse with error="..." on
    a structured backend rejection (402 Pro-required, etc.) so the caller
    can show the wall CTA instead of crashing."""
    if not cfg.api_key:
        raise HedgeError("no api key set — run `gamma login --api-key <KEY>`")

    payload_rows = [
        {
            "ticker": r.ticker,
            "asset_class": r.asset_class,
            "beta": r.beta,
            "blast_radius": r.blast_radius,
            "position_value": r.position_value,
        }
        for r in report.rows
    ]
    body = {"event_class": report.event_class, "rows": payload_rows}

    try:
        with _client(cfg, timeout=20.0) as c:
            r = c.post("/api/oracle/shock/hedge", json=body)
    except httpx.HTTPError as e:
        raise HedgeError(f"backend unreachable: {e}") from e

    if r.status_code == 402:
        # Pro-required — caller renders the upgrade CTA, not an error.
        try:
            detail = r.json().get("detail", {})
        except (ValueError, AttributeError):
            detail = {}
        return HedgeResponse(
            event_class=report.event_class, hedges=[], unhedgeable=[],
            total_hedge_notional=0.0, attestation_hash=None,
            error=(f"pro_tier_required (current: {detail.get('current_tier', 'unknown')})"),
        )
    if r.status_code == 429:
        raise HedgeError("rate-limited — try again in a minute")
    if r.status_code == 401:
        raise HedgeError("api key rejected (401) — re-run `gamma login --api-key`")
    if r.status_code >= 400:
        raise HedgeError(f"backend returned {r.status_code}: {r.text[:200]}")

    try:
        data = r.json()
    except ValueError as e:
        raise HedgeError(f"backend returned non-JSON: {r.text[:200]}") from e

    hedges = []
    for h in data.get("hedges", []):
        try:
            hedges.append(HedgeRecommendation(
                ticker=h["ticker"],
                position_value=float(h.get("position_value", 0)),
                blast_radius=float(h.get("blast_radius", 0)),
                hedge_action=h.get("hedge_action", ""),
                instrument=h.get("instrument", ""),
                sizing_pct_of_position=int(h.get("sizing_pct_of_position", 0)),
                hedge_notional=float(h.get("hedge_notional", 0)),
                rationale=h.get("rationale", ""),
                disclaimer=h.get("disclaimer", ""),
            ))
        except (KeyError, TypeError, ValueError):
            # Skip malformed rows rather than 500-ing the whole render
            continue

    return HedgeResponse(
        event_class=data.get("event_class", report.event_class),
        hedges=hedges,
        unhedgeable=data.get("unhedgeable", []),
        total_hedge_notional=float(data.get("total_hedge_notional", 0)),
        attestation_hash=data.get("attestation_hash"),
    )
