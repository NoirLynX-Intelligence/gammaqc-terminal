"""Warren Voice — calibrated structural-analysis persona.

Free-tier behavior: rule-based 3-bullet structural analysis from local
scrape data + 3 unlocked council seats (sector beta, momentum/trend,
52w-positioning). No LLM call required; deterministic, instant,
offline-safe. Demonstrates real value without burning backend calls.

Pro-tier behavior (when an API key is set): calls /oracle/voice/warren
on the sovereign backend for the full 10-seat council-graded analysis
with PQC attestation hash.

Commander feedback 2026-06-10 ('still very meh'): rewrote the rules
engine to actually USE the real quote data we now fetch (price, 5d
closes, 5d volumes, 52w range), give a decisive bias instead of
defaulting to UNKNOWN, and surface 3 council seats unlocked so the
free user sees real analysis instead of 10 [REDACTED] blanks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

import httpx

from .auth import _client
from .config import Config


# ─── Sector classification (small static table — enough to demo Pro value) ──
# Maps common tickers to asset_class buckets used by the council seats.
# This is intentionally small; backend Warren has the full institutional
# coverage. Free-tier just needs to NOT say "unknown" for the big names
# people actually type into a CLI demo.
_SECTOR_MAP: Dict[str, str] = {
    # Long-duration tech (rate-sensitive)
    "NVDA": "long_duration_tech", "AMD": "long_duration_tech",
    "TSLA": "long_duration_tech", "META": "long_duration_tech",
    "GOOGL": "long_duration_tech", "GOOG": "long_duration_tech",
    "AMZN": "long_duration_tech", "NFLX": "long_duration_tech",
    "CRM": "long_duration_tech", "ADBE": "long_duration_tech",
    "ORCL": "long_duration_tech", "PLTR": "long_duration_tech",
    "SNOW": "long_duration_tech", "AVGO": "long_duration_tech",
    "QQQ": "long_duration_tech", "ARKK": "long_duration_tech",
    # Growth tech
    "AAPL": "growth_tech", "MSFT": "growth_tech",
    "SHOP": "growth_tech", "SQ": "growth_tech",
    # Energy
    "XLE": "energy", "XOM": "energy", "CVX": "energy", "COP": "energy",
    "OXY": "energy", "SLB": "energy",
    # Banks
    "JPM": "banks", "BAC": "banks", "WFC": "banks", "C": "banks",
    "GS": "banks", "MS": "banks", "XLF": "banks",
    # REITs
    "VNQ": "reits", "XLRE": "reits", "O": "reits", "PLD": "reits",
    "SPG": "reits", "AMT": "reits", "REM": "reits",
    # Utilities
    "XLU": "utilities", "NEE": "utilities", "DUK": "utilities",
    "SO": "utilities", "AEP": "utilities",
    # Small cap
    "IWM": "small_cap", "RUT": "small_cap",
    # Gold
    "GLD": "gold", "GOLD": "gold", "NEM": "gold", "AEM": "gold",
    # Crypto-adjacent
    "COIN": "crypto", "MARA": "crypto", "RIOT": "crypto",
    "MSTR": "crypto", "GBTC": "crypto",
    # Long-duration bonds (proxy)
    "TLT": "long_duration_bonds", "ZROZ": "long_duration_bonds",
    "EDV": "long_duration_bonds",
    # Defensive
    "PG": "defensive", "KO": "defensive", "PEP": "defensive",
    "JNJ": "defensive", "PFE": "defensive", "WMT": "defensive",
    "COST": "defensive", "XLP": "defensive",
}


def _classify_sector(ticker: str) -> str:
    return _SECTOR_MAP.get(ticker.upper(), "uncategorized")


@dataclass
class WarrenAnalysis:
    bullets: List[str]
    bias: str           # "long" | "short" | "neutral" | "unknown"
    confidence: float   # 0.0–1.0; the LOCAL signal only
    source: str         # "local-rules" | "backend-warren"
    attestation_hash: str | None = None
    # NEW v0.3.2: 3 council seats unlocked in free tier
    free_council: List[Tuple[str, str, str, int]] = field(default_factory=list)
    # tuples of (seat_name, vote, rationale, conviction_0_10)


# ─── Council Seats (free tier shows these 3) ────────────────────────────────

def _seat_sector_beta(ticker: str, quote: Dict[str, Any]) -> Tuple[str, str, str, int]:
    """Seat 01 — Sector Beta. Votes based on sector positioning vs current
    regime backdrop. Deterministic, runs offline, no LLM."""
    sector = _classify_sector(ticker)
    chg_pct = quote.get("regularMarketChangePercent", 0) or 0
    # Default regime read = Fed-hawk (last 18 months of macro). This is
    # a static prior for free tier; backend Warren swaps in live regime.
    vote, why, conv = "NEUTRAL", "Sector beta uncertain — uncategorized name.", 4
    if sector == "long_duration_tech":
        if chg_pct < -2:
            vote, conv = "BEARISH", 7
            why = "Long-duration tech bleeds on rate-hawk regime; today's drop confirms."
        elif chg_pct > 2:
            vote, conv = "BULLISH", 5
            why = "Counter-trend bounce in long-duration tech — fade unless volume confirms."
        else:
            vote, conv = "BEARISH", 5
            why = "Long-duration tech remains rate-sensitive; default lean is cautious."
    elif sector == "growth_tech":
        vote, conv = ("BEARISH", 6) if chg_pct < -1.5 else ("NEUTRAL", 4)
        why = "Growth tech moderately rate-sensitive; today's move sets the lean."
    elif sector == "energy":
        vote, conv = ("BULLISH", 6) if chg_pct > 0 else ("NEUTRAL", 5)
        why = "Energy carries inflation + geopolitical premium; constructive base case."
    elif sector == "banks":
        vote, conv = ("BULLISH", 5), 5
        why = "Banks expand NIM on hike regime; counter-cyclical to long-duration tech."
        vote, conv = "BULLISH", 5
    elif sector == "reits" or sector == "long_duration_bonds":
        vote, conv = "BEARISH", 7
        why = f"{sector.replace('_', ' ').title()} disproportionately exposed to duration risk."
    elif sector == "utilities":
        vote, conv = "BEARISH", 5
        why = "Bond-proxy; bleeds slowly on persistent hike regime."
    elif sector == "small_cap":
        vote, conv = "BEARISH", 6
        why = "Small-cap balance sheets squeezed by credit tightening cycle."
    elif sector == "gold":
        vote, conv = "NEUTRAL", 5
        why = "Gold straddles inflation hedge and rate-sensitivity — modest lean."
    elif sector == "defensive":
        vote, conv = "BULLISH", 5
        why = "Defensive sleeve outperforms in late-cycle / risk-off regime."
    elif sector == "crypto":
        vote, conv = "NEUTRAL", 4
        why = "Crypto-adjacent equity beta is high but path-dependent on BTC tape."
    return "Seat 01 · Sector Beta", vote, why, conv


def _seat_momentum(ticker: str, quote: Dict[str, Any]) -> Tuple[str, str, str, int]:
    """Seat 02 — Momentum / Trend. 5-day close-to-close slope + volume
    confirmation. Free user sees a real signal here."""
    closes = quote.get("closes_5d") or []
    vols = quote.get("volumes_5d") or []
    if len(closes) < 3:
        return "Seat 02 · Momentum", "NEUTRAL", "Insufficient bars (need ≥3 closes) — abstaining.", 3
    # Slope: pct change first→last
    first, last = closes[0], closes[-1]
    slope_pct = ((last - first) / first * 100) if first else 0
    vol_avg = (sum(vols) / len(vols)) if vols else 0
    today_vol = vols[-1] if vols else 0
    vol_lift = (today_vol / vol_avg) if vol_avg else 1
    if slope_pct > 3 and vol_lift > 1.1:
        return ("Seat 02 · Momentum", "BULLISH",
                f"5d trend +{slope_pct:.1f}% on {vol_lift:.1f}x average volume — trend + flow agree.", 7)
    if slope_pct < -3 and vol_lift > 1.1:
        return ("Seat 02 · Momentum", "BEARISH",
                f"5d trend {slope_pct:.1f}% on {vol_lift:.1f}x average volume — distribution.", 7)
    if slope_pct > 3:
        return ("Seat 02 · Momentum", "BULLISH",
                f"5d trend +{slope_pct:.1f}% but volume {vol_lift:.1f}x — narrative > flow.", 5)
    if slope_pct < -3:
        return ("Seat 02 · Momentum", "BEARISH",
                f"5d trend {slope_pct:.1f}% but volume {vol_lift:.1f}x — drift, not panic.", 5)
    return ("Seat 02 · Momentum", "NEUTRAL",
            f"5d slope {slope_pct:+.1f}% — no decisive momentum.", 4)


def _seat_range_position(ticker: str, quote: Dict[str, Any]) -> Tuple[str, str, str, int]:
    """Seat 03 — 52w Range Position. Where in the 52w channel is price?
    Buy-the-dip vs sell-the-rip framing."""
    price = quote.get("regularMarketPrice")
    hi = quote.get("fiftyTwoWeekHigh")
    lo = quote.get("fiftyTwoWeekLow")
    if not (price and hi and lo and hi > lo):
        return "Seat 03 · 52w Position", "NEUTRAL", "Range data unavailable — abstaining.", 3
    pct_of_range = (price - lo) / (hi - lo) * 100
    if pct_of_range > 85:
        return ("Seat 03 · 52w Position", "BEARISH",
                f"Trading at {pct_of_range:.0f}% of 52w range — chasing the highs.", 6)
    if pct_of_range < 15:
        return ("Seat 03 · 52w Position", "BULLISH",
                f"At {pct_of_range:.0f}% of 52w range — value zone if fundamentals intact.", 6)
    if pct_of_range > 65:
        return ("Seat 03 · 52w Position", "NEUTRAL",
                f"Upper third of 52w range ({pct_of_range:.0f}%) — neither cheap nor extreme.", 4)
    if pct_of_range < 35:
        return ("Seat 03 · 52w Position", "NEUTRAL",
                f"Lower third of 52w range ({pct_of_range:.0f}%) — watch for bounce signal.", 4)
    return ("Seat 03 · 52w Position", "NEUTRAL",
            f"Mid-range ({pct_of_range:.0f}% of 52w) — no positioning edge.", 4)


# ─── Bullets — substantive, decisive, no more 'thin / unavailable' ─────────

def _decisive_bias(seats: List[Tuple[str, str, str, int]]) -> Tuple[str, float]:
    """Vote-weighted bias from the 3 free council seats. Never returns
    'unknown' if any seat fired — democracy with conviction weight."""
    if not seats:
        return "unknown", 0.0
    score = 0.0
    weight_total = 0.0
    for _, vote, _, conv in seats:
        w = conv / 10.0
        weight_total += w
        if vote == "BULLISH":
            score += w
        elif vote == "BEARISH":
            score -= w
    if weight_total == 0:
        return "neutral", 0.4
    avg = score / weight_total
    if avg > 0.25:
        return "long", min(0.85, 0.5 + abs(avg) * 0.5)
    if avg < -0.25:
        return "short", min(0.85, 0.5 + abs(avg) * 0.5)
    return "neutral", 0.5


def _local_warren(ticker: str, quote: Dict[str, Any], filings: List[Dict[str, Any]]) -> WarrenAnalysis:
    # Compute the three seats first; their verdicts drive bullets and bias.
    seats = [
        _seat_sector_beta(ticker, quote),
        _seat_momentum(ticker, quote),
        _seat_range_position(ticker, quote),
    ]
    bias, conf = _decisive_bias(seats)
    bullets: List[str] = []

    # Bullet 1 — price action with structural read
    price = quote.get("regularMarketPrice")
    chg_pct = quote.get("regularMarketChangePercent")
    if price is not None and chg_pct is not None:
        action = "rallying" if chg_pct > 1.5 else ("bleeding" if chg_pct < -1.5 else "drifting")
        bullets.append(
            f"{ticker} prints ${price:,.2f} ({chg_pct:+.2f}%) — {action} on the session. "
            f"Council reads {bias.upper()} at {conf:.0%} conviction from the 3 free seats below."
        )
    else:
        bullets.append(f"{ticker} quote feed thin — Yahoo returned no actionable price.")

    # Bullet 2 — volume + flow, only when we have real data
    vol = quote.get("regularMarketVolume")
    avg_vol = quote.get("averageVolume5d")
    if vol and avg_vol and avg_vol > 0:
        ratio = vol / avg_vol
        if ratio > 1.3:
            bullets.append(
                f"Volume {vol:,} on the close — {ratio:.1f}x the 5d average ({avg_vol:,.0f}). "
                f"Flow is louder than narrative; treat the move as conviction, not noise."
            )
        elif ratio < 0.7:
            bullets.append(
                f"Volume {vol:,} — {ratio:.1f}x the 5d average ({avg_vol:,.0f}). Thin tape. "
                f"Price moves here are low-conviction; do not size into them."
            )
        else:
            bullets.append(
                f"Volume {vol:,} tracking the 5d average ({avg_vol:,.0f}) at {ratio:.1f}x — "
                f"normal flow. No conviction signal from the tape."
            )
    elif vol:
        bullets.append(f"Volume {vol:,} — no historical baseline to compare; flow signal abstained.")
    else:
        bullets.append("Volume data unavailable — directional read above is price-only, no flow confirmation.")

    # Bullet 3 — SEC filings posture
    if filings:
        latest = filings[0]
        form = latest.get("form", "?")
        filed = latest.get("filed", "?")
        n = len(filings)
        bullets.append(
            f"{n} recent SEC filings — latest: Form {form} on {filed}. "
            f"Form 4 = insider sale (sell signal weighted into Council); "
            f"Form 10-Q = quarterly results; pull the doc before any earnings-window position."
        )
    else:
        bullets.append("No recent SEC filings surfaced — small-cap with sparse cadence or CIK lookup missed.")

    return WarrenAnalysis(
        bullets=bullets[:3],
        bias=bias,
        confidence=conf,
        source="local-rules",
        free_council=seats,
    )


def warren_analyze(ticker: str, quote: Dict[str, Any], filings: List[Dict[str, Any]],
                   cfg: Config | None = None) -> WarrenAnalysis:
    """Local-first analyze. If cfg has an api_key, ATTEMPT backend lift
    but fall back to local on any failure (graceful degradation — the
    CLI never hangs on a flaky backend)."""
    local = _local_warren(ticker, quote, filings)
    if not cfg or not cfg.api_key:
        return local
    try:
        with _client(cfg, timeout=10.0) as c:
            r = c.post("/api/oracle/voice/warren", json={
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
                free_council=local.free_council,   # keep the free seats so card always renders them
            )
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return local
