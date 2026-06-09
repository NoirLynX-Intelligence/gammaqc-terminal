"""Ghost-Watcher — headless market-condition daemon.

Free-tier behavior: detaches a polling loop that hits public quote
endpoints on the user's local interval (default 30s), evaluates a
trigger expression, and fires a native desktop notification + optional
webhook ping when the condition is met. The daemon runs IN THE USER'S
PROCESS — we don't deploy anything to their machine they can't kill
with Ctrl-C. No telemetry, no remote-mutable state.

Pro-tier behavior: when an API key is set, satisfied triggers ALSO post
to /oracle/watch/receipt which generates a PQC-sealed trade-prep receipt
(the legally-compliant risk log the Wall surfaces).

Trigger expressions (v0.1):
- `price > X` / `price < X`
- `pct_change > X` / `pct_change < X`   (intraday change in percent)
- `volume_spike > X`                    (current_vol / 3mo_avg ratio)
- `options_volume_spike > X`            (PRO ONLY — requires CBOE feed
  through backend; free tier degrades to a warning saying so)
"""
from __future__ import annotations

import asyncio
import json
import operator
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .auth import _client
from .config import Config

_YAHOO_QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
_OP_TABLE: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt, "<": operator.lt,
    ">=": operator.ge, "<=": operator.le,
    "==": operator.eq,
}
# v0.1 supports a single-clause trigger: `<metric> <op> <value>`
_TRIGGER_RE = re.compile(
    r"^\s*(price|pct_change|volume_spike|options_volume_spike)\s*"
    r"(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


@dataclass
class TriggerSpec:
    metric: str
    op_symbol: str
    threshold: float

    @classmethod
    def parse(cls, expr: str) -> "TriggerSpec":
        m = _TRIGGER_RE.match(expr)
        if not m:
            raise ValueError(
                f"unparseable trigger: {expr!r}. "
                "Expected: '<metric> <op> <value>' where metric is "
                "price|pct_change|volume_spike|options_volume_spike "
                "and op is one of > < >= <= =="
            )
        return cls(metric=m.group(1).lower(), op_symbol=m.group(2), threshold=float(m.group(3)))

    @property
    def op(self) -> Callable[[float, float], bool]:
        return _OP_TABLE[self.op_symbol]

    def __str__(self) -> str:
        return f"{self.metric} {self.op_symbol} {self.threshold}"


async def _quote_async(client: httpx.AsyncClient, ticker: str) -> dict[str, Any]:
    try:
        r = await client.get(_YAHOO_QUOTE, params={"symbols": ticker}, timeout=10)
        if r.status_code != 200:
            return {}
        results = r.json().get("quoteResponse", {}).get("result", [])
        return results[0] if results else {}
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError):
        return {}


def _extract_metric(metric: str, q: dict[str, Any]) -> float | None:
    if metric == "price":
        return q.get("regularMarketPrice")
    if metric == "pct_change":
        return q.get("regularMarketChangePercent")
    if metric == "volume_spike":
        v = q.get("regularMarketVolume", 0) or 0
        a = q.get("averageDailyVolume3Month", 0) or 0
        return (v / a) if a else None
    # options_volume_spike requires CBOE feed → Pro only
    return None


def _notify_desktop(title: str, body: str) -> bool:
    """Best-effort native notification via plyer. Returns True on success.
    Watch dependency is optional — install with `pip install
    gammaqc-terminal[watch]`."""
    try:
        from plyer import notification   # type: ignore
        notification.notify(title=title, message=body[:240], timeout=10)
        return True
    except Exception:
        return False


def _post_webhook(url: str, payload: dict[str, Any]) -> bool:
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(url, json=payload)
        return r.status_code < 400
    except httpx.HTTPError:
        return False


async def watch_loop(ticker: str, spec: TriggerSpec, *,
                     interval_s: int = 30,
                     webhook_url: str | None = None,
                     cfg: Config | None = None,
                     on_event: Callable[[str], None] | None = None) -> None:
    """Async polling loop. Cancel via KeyboardInterrupt / asyncio cancel.

    on_event: optional callback for test instrumentation; receives a
    one-line status string each tick."""
    if spec.metric == "options_volume_spike" and (not cfg or not cfg.api_key):
        raise ValueError(
            "options_volume_spike requires a Pro API key (CBOE feed). "
            "Authenticate with `gamma login --api-key <KEY>`."
        )

    fired = False
    tick = 0
    async with httpx.AsyncClient(headers={"user-agent": "gammaqc-terminal/0.1"}) as client:
        while not fired:
            tick += 1
            q = await _quote_async(client, ticker)
            value = _extract_metric(spec.metric, q)
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if value is None:
                msg = f"[tick {tick} @ {ts}] {ticker}: metric '{spec.metric}' unavailable upstream"
                if on_event:
                    on_event(msg)
                await asyncio.sleep(interval_s)
                continue
            hit = spec.op(value, spec.threshold)
            msg = f"[tick {tick} @ {ts}] {ticker}: {spec.metric}={value:.4f} ({spec})  → {'FIRE' if hit else 'wait'}"
            if on_event:
                on_event(msg)
            if hit:
                fired = True
                body = f"{ticker}: {spec.metric}={value:.4f} crossed {spec.op_symbol}{spec.threshold} at {ts}"
                _notify_desktop(f"GammaQC Ghost-Watcher: {ticker}", body)
                if webhook_url:
                    _post_webhook(webhook_url, {
                        "ticker": ticker, "metric": spec.metric, "value": value,
                        "threshold": spec.threshold, "op": spec.op_symbol,
                        "fired_at": ts, "source": "gammaqc-terminal/ghost-watcher",
                    })
                # Pro lift — request a PQC-sealed receipt.
                if cfg and cfg.api_key:
                    try:
                        with _client(cfg, timeout=15) as c:
                            c.post("/oracle/watch/receipt", json={
                                "ticker": ticker,
                                "trigger": str(spec),
                                "fired_value": value,
                                "fired_at": ts,
                            })
                    except httpx.HTTPError:
                        pass
                break
            await asyncio.sleep(interval_s)


def run_watch(ticker: str, trigger_expr: str, *,
              interval_s: int = 30, webhook_url: str | None = None,
              cfg: Config | None = None,
              on_event: Callable[[str], None] | None = None) -> None:
    """Synchronous wrapper that handles SIGINT cleanly so users can
    Ctrl-C without a stack trace."""
    spec = TriggerSpec.parse(trigger_expr)
    loop = asyncio.new_event_loop()

    def _sig_handler(*_a):
        for t in asyncio.all_tasks(loop):
            t.cancel()
    try:
        signal.signal(signal.SIGINT, _sig_handler)
    except (ValueError, AttributeError):
        pass   # not in main thread / windows quirk

    try:
        loop.run_until_complete(watch_loop(
            ticker, spec, interval_s=interval_s,
            webhook_url=webhook_url, cfg=cfg, on_event=on_event,
        ))
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
