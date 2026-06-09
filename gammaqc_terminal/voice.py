"""Warren Voice — calibrated structural-analysis persona.

Free-tier behavior: rule-based 3-bullet structural analysis from local
scrape data. No LLM call required; deterministic, instant, offline-safe.
Demonstrates "this isn't a generic ChatGPT wrapper" without burning
backend calls on free-tier traffic.

Pro-tier behavior (when an API key is set): calls /oracle/voice/warren
on the sovereign backend for the full council-graded analysis with
attestation hash. The local rule-based output is shown alongside the
backend output with a clear marker so the user sees the lift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .auth import _client
from .config import Config


@dataclass
class WarrenAnalysis:
    bullets: list[str]
    bias: str           # "long" | "short" | "neutral" | "unknown"
    confidence: float   # 0.0–1.0; the LOCAL signal only
    source: str         # "local-rules" | "backend-warren"
    attestation_hash: str | None = None


def _bias_from_quote(q: dict[str, Any]) -> tuple[str, float]:
    """Cheap directional signal from intraday quote data. Honest about
    its limits — momentum-only, no fundamentals, no flow data."""
    chg_pct = q.get("regularMarketChangePercent")
    vol = q.get("regularMarketVolume", 0) or 0
    avg_vol = q.get("averageDailyVolume3Month", 0) or 0
    if chg_pct is None:
        return "unknown", 0.0
    vol_lift = (vol / avg_vol) if avg_vol > 0 else 1.0
    if chg_pct > 1.5 and vol_lift > 1.2:
        return "long", min(0.75, 0.4 + (vol_lift - 1) * 0.3)
    if chg_pct < -1.5 and vol_lift > 1.2:
        return "short", min(0.75, 0.4 + (vol_lift - 1) * 0.3)
    if abs(chg_pct) <= 1.5:
        return "neutral", 0.5
    return "long" if chg_pct > 0 else "short", 0.4


def _local_warren(ticker: str, quote: dict[str, Any], filings: list[dict[str, Any]]) -> WarrenAnalysis:
    bias, conf = _bias_from_quote(quote)
    bullets: list[str] = []

    price = quote.get("regularMarketPrice")
    chg_pct = quote.get("regularMarketChangePercent")
    if price is not None and chg_pct is not None:
        bullets.append(
            f"{ticker} prints ${price:,.2f} ({chg_pct:+.2f}% intraday) — "
            f"structural bias reads {bias} on momentum alone."
        )
    elif price is not None:
        bullets.append(f"{ticker} last ${price:,.2f}; intraday change not surfaced by quote feed.")
    else:
        bullets.append(f"{ticker} quote feed thin — Yahoo returned no actionable price.")

    vol = quote.get("regularMarketVolume")
    avg_vol = quote.get("averageDailyVolume3Month")
    if vol and avg_vol:
        ratio = vol / avg_vol
        if ratio > 1.5:
            bullets.append(
                f"Volume at {ratio:.1f}x 3-month average — flow is *louder* than narrative "
                f"({vol:,} vs {avg_vol:,} avg). Treat as conviction-weighted, not noise."
            )
        elif ratio < 0.6:
            bullets.append(
                f"Volume at {ratio:.1f}x 3-month average — thin tape. "
                f"Price moves here are low-conviction; size accordingly."
            )
        else:
            bullets.append(f"Volume ({vol:,}) tracking close to 3-month average ({avg_vol:,}) — no flow anomaly.")
    else:
        bullets.append("Volume data unavailable — directional read above is price-only, no flow confirmation.")

    if filings:
        latest = filings[0]
        bullets.append(
            f"Most recent SEC filing: {latest.get('form', '?')} dated {latest.get('filed', '?')} "
            f"(accession {latest.get('accession', '?')[:20]}…). "
            f"Pull the actual document before any earnings-window position."
        )
    else:
        bullets.append("No recent SEC filings surfaced — either small-cap with sparse cadence or CIK lookup failed.")

    return WarrenAnalysis(
        bullets=bullets[:3],
        bias=bias,
        confidence=conf,
        source="local-rules",
    )


def warren_analyze(ticker: str, quote: dict[str, Any], filings: list[dict[str, Any]],
                   cfg: Config | None = None) -> WarrenAnalysis:
    """Local-first analyze. If cfg has an api_key, ATTEMPT backend lift
    but fall back to local on any failure (graceful degradation — the
    CLI never hangs on a flaky backend)."""
    local = _local_warren(ticker, quote, filings)
    if not cfg or not cfg.api_key:
        return local
    try:
        with _client(cfg, timeout=10.0) as c:
            r = c.post("/oracle/voice/warren", json={
                "ticker": ticker, "quote": quote, "filings": filings,
            })
        if r.status_code == 200:
            payload = r.json()
            return WarrenAnalysis(
                bullets=payload.get("bullets", local.bullets)[:3],
                bias=payload.get("bias", local.bias),
                confidence=float(payload.get("confidence", local.confidence)),
                source="backend-warren",
                attestation_hash=payload.get("attestation_hash"),
            )
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return local
