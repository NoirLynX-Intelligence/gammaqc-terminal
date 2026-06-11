"""Raw SEC + market-data scraper.

Free-tier behavior (no API key): pulls public SEC EDGAR filings + Yahoo
Finance quote data directly from the user's machine. No middleman. The
raw file never leaves their local box — privacy is a feature, not a
constraint.

EDGAR endpoints are intentionally unauthenticated public APIs; SEC asks
for a descriptive User-Agent (with contact email) per their fair-access
policy. We honor that — see _SEC_UA below.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

_SEC_UA = "gammaqc-terminal/0.3 (contact: ops@gammaqc.com)"
_SEC_BASE = "https://data.sec.gov"
# v7/quote started requiring crumb+cookie auth in 2024 — unauthenticated
# requests fail silently. v8/chart still serves unauthenticated and returns
# price, volume, market_cap, day_range, and meta in one call. We pluck the
# fields we need and shape them into the same dict the rest of the CLI
# already expects so renderers don't change.
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# SEC asks for ≤10 req/s; we pace conservatively at ~5 req/s with sleeps
# between calls. No need for async — a quote + filings call is 3 HTTPs.
_REQ_SPACING_S = 0.2


@dataclass
class ScrapeResult:
    ticker: str
    quote: dict[str, Any]
    recent_filings: list[dict[str, Any]]
    cik: str | None
    error: str | None = None


def _cik_for_ticker(client: httpx.Client, ticker: str) -> str | None:
    """SEC publishes a ticker→CIK index. Cache on disk in a future rev;
    for v0.1 we fetch fresh per call (~50KB)."""
    try:
        r = client.get("https://www.sec.gov/files/company_tickers.json", timeout=15)
        if r.status_code != 200:
            return None
        idx = r.json()
        ticker_u = ticker.upper()
        for _, row in idx.items():
            if row.get("ticker", "").upper() == ticker_u:
                cik = str(row.get("cik_str", ""))
                return cik.zfill(10) if cik else None
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        pass
    return None


def _recent_filings(client: httpx.Client, cik: str, limit: int = 5) -> list[dict[str, Any]]:
    try:
        r = client.get(f"{_SEC_BASE}/submissions/CIK{cik}.json", timeout=15)
        if r.status_code != 200:
            return []
        sub = r.json()
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accs = recent.get("accessionNumber", [])
        out = []
        for i in range(min(len(forms), limit)):
            out.append({
                "form": forms[i],
                "filed": dates[i] if i < len(dates) else "",
                "accession": accs[i] if i < len(accs) else "",
            })
        return out
    except (httpx.HTTPError, json.JSONDecodeError, KeyError):
        return []


def _quote(client: httpx.Client, ticker: str) -> dict[str, Any]:
    """Yahoo chart endpoint — unauthenticated, no crumb required (unlike
    v7/quote which requires a session cookie + crumb since 2024). Returns
    a dict shaped to match what the rest of the CLI expects so renderers
    don't have to change. Returns {} on failure."""
    try:
        url = _YAHOO_CHART.format(symbol=ticker.upper())
        r = client.get(url, params={"interval": "1d", "range": "5d"}, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
        chart = data.get("chart", {})
        results = chart.get("result", [])
        if not results:
            return {}
        result = results[0]
        meta = result.get("meta", {}) or {}
        indicators = (result.get("indicators", {}) or {}).get("quote", [{}])
        q = indicators[0] if indicators else {}
        # Last non-null close + volume from the most recent bar
        closes = [c for c in (q.get("close") or []) if c is not None]
        vols = [v for v in (q.get("volume") or []) if v is not None]
        last_close = closes[-1] if closes else meta.get("regularMarketPrice")
        last_vol = vols[-1] if vols else 0
        prev_close = meta.get("chartPreviousClose") or (closes[-2] if len(closes) >= 2 else last_close)
        change = (last_close - prev_close) if (last_close and prev_close) else 0
        change_pct = (change / prev_close * 100) if prev_close else 0
        # 5-day average volume — Warren's flow signal needs a baseline to
        # compare "today" against. Without this we can't say "volume is
        # louder than narrative" with any real meaning.
        avg_vol_5d = (sum(vols) / len(vols)) if vols else 0
        # Shape into the dict the renderers/voice/card already expect
        return {
            "symbol": meta.get("symbol", ticker.upper()),
            "shortName": meta.get("shortName") or meta.get("longName") or ticker.upper(),
            "regularMarketPrice": last_close,
            "regularMarketChange": change,
            "regularMarketChangePercent": change_pct,
            "regularMarketVolume": last_vol,
            "regularMarketDayHigh": meta.get("regularMarketDayHigh"),
            "regularMarketDayLow": meta.get("regularMarketDayLow"),
            "fiftyTwoWeekHigh": meta.get("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": meta.get("fiftyTwoWeekLow"),
            "marketCap": meta.get("marketCap"),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
            # NEW v0.3.2: actual history so voice + card can compute real signals
            "closes_5d": closes,
            "volumes_5d": vols,
            "averageVolume5d": avg_vol_5d,
            "previousClose": prev_close,
        }
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, ValueError, TypeError):
        return {}


def scrape_ticker(ticker: str) -> ScrapeResult:
    """One-shot scrape: quote + recent SEC filings. All public endpoints,
    no API key required, no third-party telemetry."""
    headers = {
        "user-agent": _SEC_UA,
        "accept-encoding": "gzip, deflate",
    }
    with httpx.Client(headers=headers) as client:
        cik = _cik_for_ticker(client, ticker)
        time.sleep(_REQ_SPACING_S)
        quote = _quote(client, ticker)
        time.sleep(_REQ_SPACING_S)
        filings = _recent_filings(client, cik) if cik else []
    if not quote and not filings:
        return ScrapeResult(
            ticker=ticker.upper(), quote={}, recent_filings=[], cik=cik,
            error="no data — invalid ticker or upstream rate-limit",
        )
    return ScrapeResult(ticker=ticker.upper(), quote=quote, recent_filings=filings, cik=cik)
