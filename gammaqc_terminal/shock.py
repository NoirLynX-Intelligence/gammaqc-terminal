"""Portfolio Shock Matrix — local stress-tester for retail portfolios.

Honesty contract:
- The user's holdings CSV NEVER leaves their local machine in free mode.
  We parse it client-side, compute per-ticker shock exposure locally,
  and surface the Blast Radius Report inline. Privacy is the product.
- Pro tier adds Algorithmic Hedge Strategy generation via the backend
  Council. Hedge generation requires sending TICKERS (not positions/
  dollar amounts) to /oracle/shock/hedge — the wall is in /Pro tier,
  but the privacy floor (no dollar exposure leaves the box) holds.

Heuristic model (v0.1, free tier):
- Event keyword → asset-class beta vector (rates / oil / FX / risk-off)
- Each ticker mapped to asset class via lightweight rule (sector cue
  in the CSV `sector` column if present, else hard-coded for common
  tickers, else 'unknown' → neutral exposure).
- Blast Radius = position_value × class_beta_to_event.

This is intentionally a heuristic, not a regression model — it surfaces
DIRECTIONAL exposure ("which positions bleed on Fed +50bps") not P&L
forecasts. Honest about its limits.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Asset-class beta vectors per event keyword. Values are directional
# multipliers in [-1.5, +1.5]; positive = position GAINS on event.
EVENT_BETAS: dict[str, dict[str, float]] = {
    # Fed hawkish / rate-hike events
    "fed_hike": {
        "long_duration_tech": -1.2,
        "growth_tech":        -0.9,
        "financials":         +0.6,
        "energy":             +0.3,
        "utilities":          -0.7,
        "reits":              -1.0,
        "gold":               -0.5,
        "crypto":             -0.8,
        "small_cap":          -0.8,
        "value":              -0.2,
        "unknown":             0.0,
    },
    # CPI hot / inflation surprise
    "cpi_hot": {
        "long_duration_tech": -1.0,
        "growth_tech":        -0.7,
        "financials":         +0.4,
        "energy":             +0.8,
        "utilities":          -0.4,
        "reits":              -0.6,
        "gold":               +0.5,
        "crypto":             -0.3,
        "small_cap":          -0.5,
        "value":              +0.1,
        "unknown":             0.0,
    },
    # Geopolitical risk-off (war, sanctions, supply shock)
    "geopolitical": {
        "long_duration_tech": -0.4,
        "growth_tech":        -0.5,
        "financials":         -0.3,
        "energy":             +1.0,
        "utilities":          +0.3,
        "reits":              -0.2,
        "gold":               +1.2,
        "crypto":              0.0,
        "small_cap":          -0.7,
        "value":              -0.3,
        "unknown":             0.0,
    },
}

# Coarse sector classifier — used when CSV doesn't have a `sector` column.
# Intentionally short (top tickers); falls back to 'unknown' otherwise.
TICKER_CLASS: dict[str, str] = {
    "NVDA": "growth_tech", "AAPL": "growth_tech", "MSFT": "growth_tech",
    "GOOGL": "growth_tech", "META": "growth_tech", "AMZN": "growth_tech",
    "TSLA": "growth_tech", "AMD": "growth_tech", "NFLX": "growth_tech",
    "JPM": "financials", "BAC": "financials", "WFC": "financials",
    "GS": "financials", "MS": "financials", "C": "financials",
    "XOM": "energy", "CVX": "energy", "COP": "energy", "OXY": "energy",
    "NEE": "utilities", "DUK": "utilities", "SO": "utilities",
    "O": "reits", "PLD": "reits", "AMT": "reits", "SPG": "reits",
    "GLD": "gold", "IAU": "gold", "GDX": "gold",
    "BTC": "crypto", "ETH": "crypto", "COIN": "crypto", "MSTR": "crypto",
    "IWM": "small_cap",
    "BRK.B": "value", "BRK-B": "value",
}


def _classify_event(event_text: str) -> str:
    """Return the EVENT_BETAS key best matching the user's plaintext.
    Conservative — defaults to fed_hike if ambiguous, since that's the
    most common Q&A on rate-cycle questions."""
    t = event_text.lower()
    if any(w in t for w in ["cpi", "inflation", "ppi"]):
        return "cpi_hot"
    if any(w in t for w in ["war", "sanction", "missile", "tariff", "embargo",
                            "geopolit", "iran", "russia", "ukraine", "taiwan"]):
        return "geopolitical"
    return "fed_hike"   # default — covers "Fed raises", "rate hike", "FOMC", etc.


@dataclass
class ShockRow:
    ticker: str
    position_value: float
    asset_class: str
    beta: float
    blast_radius: float        # signed dollars expected to move (heuristic)

    @property
    def direction(self) -> str:
        if self.blast_radius > 0:
            return "GAIN"
        if self.blast_radius < 0:
            return "BLEED"
        return "FLAT"


@dataclass
class ShockReport:
    event_text: str
    event_class: str
    rows: list[ShockRow]
    total_position: float
    net_blast_radius: float
    warnings: list[str]


def load_holdings(path: Path) -> list[dict[str, Any]]:
    """Parse holdings CSV. Required columns: ticker, value (or qty+price).
    Optional: sector. Robust to leading-zero whitespace and quote chars."""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        lower_field = {fn.lower().strip(): fn for fn in reader.fieldnames}
        ticker_col = lower_field.get("ticker") or lower_field.get("symbol")
        value_col = lower_field.get("value") or lower_field.get("market_value") or lower_field.get("mkt_value")
        qty_col = lower_field.get("qty") or lower_field.get("quantity") or lower_field.get("shares")
        price_col = lower_field.get("price") or lower_field.get("last_price")
        sector_col = lower_field.get("sector") or lower_field.get("asset_class")
        if not ticker_col:
            raise ValueError("holdings CSV must have a 'ticker' or 'symbol' column")
        for raw in reader:
            tk = (raw.get(ticker_col) or "").strip().upper()
            if not tk:
                continue
            value: float | None = None
            if value_col:
                try:
                    value = float(str(raw.get(value_col, "")).replace(",", "").replace("$", "") or 0)
                except ValueError:
                    value = None
            if value is None and qty_col and price_col:
                try:
                    value = float(raw.get(qty_col, 0) or 0) * float(raw.get(price_col, 0) or 0)
                except ValueError:
                    value = 0.0
            sector = (raw.get(sector_col) or "").strip().lower() if sector_col else ""
            rows.append({"ticker": tk, "value": value or 0.0, "sector": sector})
    return rows


def _asset_class_for(row: dict[str, Any]) -> str:
    """Prefer the CSV's `sector` if it maps to a known class; else lookup
    the ticker; else 'unknown'."""
    sector_hint = row.get("sector", "").lower()
    if sector_hint:
        # Normalize a few common labels.
        if "tech" in sector_hint:
            return "growth_tech"
        if "financ" in sector_hint or "bank" in sector_hint:
            return "financials"
        if "energy" in sector_hint or "oil" in sector_hint:
            return "energy"
        if "util" in sector_hint:
            return "utilities"
        if "reit" in sector_hint or "real estate" in sector_hint:
            return "reits"
        if "gold" in sector_hint or "metal" in sector_hint:
            return "gold"
        if "crypto" in sector_hint or "digital asset" in sector_hint:
            return "crypto"
        if "small" in sector_hint:
            return "small_cap"
        if "value" in sector_hint:
            return "value"
    return TICKER_CLASS.get(row["ticker"], "unknown")


def run_shock(holdings_path: Path, event_text: str) -> ShockReport:
    raw_rows = load_holdings(holdings_path)
    event_class = _classify_event(event_text)
    betas = EVENT_BETAS[event_class]
    rows: list[ShockRow] = []
    warnings: list[str] = []
    unknown_count = 0
    for raw in raw_rows:
        cls = _asset_class_for(raw)
        if cls == "unknown":
            unknown_count += 1
        beta = betas.get(cls, 0.0)
        blast = raw["value"] * beta
        rows.append(ShockRow(
            ticker=raw["ticker"], position_value=raw["value"],
            asset_class=cls, beta=beta, blast_radius=blast,
        ))
    if unknown_count:
        warnings.append(
            f"{unknown_count} position(s) lacked a sector classification — treated as "
            f"NEUTRAL (β=0). Add a `sector` column to your CSV for sharper signal."
        )
    return ShockReport(
        event_text=event_text,
        event_class=event_class,
        rows=sorted(rows, key=lambda r: r.blast_radius),   # bleeders first
        total_position=sum(r.position_value for r in rows),
        net_blast_radius=sum(r.blast_radius for r in rows),
        warnings=warnings,
    )
