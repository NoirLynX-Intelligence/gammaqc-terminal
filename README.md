# gammaqc-terminal

> Sovereign quantitative-finance CLI. Free at the surface. PQC-sealed at the wall.

```bash
curl -sL https://gammaqc.com/install | bash
# or, from any of the 500 sovereign mesh domains, e.g.
curl -sL https://tech-earnings-cli.xyz/install | bash
```

Then:

```bash
gamma analyze NVDA
gamma shock --portfolio ./holdings.csv --event "Fed raises rates 50bps"
gamma watch NVDA --trigger "volume_spike > 3.0"
```

---

## Why this exists

Bloomberg costs $2,400/month. The free tools (Yahoo, TradingView) are passive
lookup engines. Quants and independent RIAs have neither budget for the former
nor patience for the latter.

`gamma` is the missing layer: a local terminal that does the active things
those tools won't. It pulls public SEC filings + quote data directly to your
machine, runs a Warren-Voice structural read on what it sees, stress-tests
your local portfolio CSV against macro events, and runs a Ghost-Watcher
daemon while you sleep.

The free tier is genuinely useful standalone. The Pro tier (`gammaqc.com/pro`)
adds the institutional layer — 10-Seat Sacred Council consensus voting,
PQC-sealed compliance receipts, Algorithmic Hedge Strategy generation.

---

## Commands

| Command | What it does | Free | Pro |
|---|---|---|---|
| `gamma analyze <TICKER>` | SEC filings + quote + Warren-Voice + Trader Card | Local rule-based bullets, ASCII card | Backend council bullets + sealed card |
| `gamma scrape <TICKER>` | Raw JSON to stdout (pipe-friendly) | ✓ | ✓ |
| `gamma card <TICKER>` | Trader Card render only | Locked council, redacted receipt | Full council split, PQC witness receipt |
| `gamma shock -p <CSV> -e <EVENT>` | Portfolio Shock Matrix → Blast Radius Report | Per-position β + bleeders surfaced | + Algorithmic Hedge Strategy |
| `gamma watch <TICKER> -t <EXPR>` | Headless Ghost-Watcher daemon | Native notification + webhook on fire | + Auto-generated PQC trade-prep receipt |
| `gamma login --api-key <KEY>` | Bind to backend | — | — |
| `gamma logout` | Clear local key | — | — |
| `gamma status` | Show config + auth state | — | — |

---

## Privacy contract

Three honesty boundaries:

1. **Your holdings CSV NEVER leaves your machine** in free mode. `shock`
   parses it locally and computes Blast Radius locally. No telemetry, no
   uploads. Run with the network off — it still works.
2. **The install script does not phone home.** The only network call is
   the package download (PyPI or the `GAMMAQC_INSTALL_URL` override).
3. **API keys are stored at the OS-native config dir** (`platformdirs`)
   with 0600 perms on POSIX. Never logged, never echoed, never sent
   anywhere except `Authorization: Bearer ...` over TLS to your
   configured backend (default `api.gammaqc.com`).

---

## Trigger expressions (Ghost-Watcher)

Single-clause expressions, format: `<metric> <op> <value>`.

| Metric | Tier | Description |
|---|---|---|
| `price` | Free | Last regular-market price (USD) |
| `pct_change` | Free | Intraday % change vs prior close |
| `volume_spike` | Free | Current volume / 3-month average |
| `options_volume_spike` | **Pro** | Options volume / 30-day average (CBOE feed) |

Operators: `>`, `<`, `>=`, `<=`, `==`.

```bash
gamma watch NVDA --trigger "pct_change < -3.0" --interval 60
gamma watch SPY  --trigger "volume_spike > 2.0" --webhook https://discord.com/api/webhooks/...
gamma watch TSLA --trigger "options_volume_spike > 3.0"   # Pro tier
```

---

## CSV format (`gamma shock`)

Required column: `ticker` (or `symbol`).

Required ONE of:
- `value` (or `market_value` / `mkt_value`) — dollar position size
- `qty` + `price` (or `quantity` / `shares` + `last_price`)

Optional column: `sector` — improves Blast Radius accuracy when the
ticker isn't in the built-in classification table.

```csv
ticker,value,sector
NVDA,15000,tech
JPM,8000,financials
XOM,5000,energy
```

See [`examples/holdings.sample.csv`](examples/holdings.sample.csv).

---

## Architecture

```
┌──────────────────────────────────────────────┐
│ gammaqc-terminal (this repo, Apache-2.0)     │
│ ┌────────┐ ┌────────┐ ┌──────┐ ┌──────────┐  │
│ │analyze │ │scrape  │ │shock │ │watch     │  │
│ └────┬───┘ └────┬───┘ └──┬───┘ └────┬─────┘  │
│      │          │        │           │        │
│   scraper.py  scraper  shock.py   watch.py    │
│      │                              │         │
│   voice.py  (local Warren rules)    │         │
│      └────────┬──────────┬──────────┘         │
│         (optional)  auth.py                    │
└─────────────────────┼─────────────────────────┘
                      │ HTTPS + Bearer key
                      ▼
        ┌─────────────────────────────────┐
        │ api.gammaqc.com (sovereign)     │
        │  /oracle/voice/warren           │
        │  /oracle/card/sealed            │
        │  /oracle/shock/hedge            │
        │  /oracle/watch/receipt          │
        │  /oracle/auth/validate          │
        └─────────────────────────────────┘
```

---

## Development

```bash
git clone https://github.com/NoirLynX-Intelligence/gammaqc-terminal
cd gammaqc-terminal
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,watch]"
pytest
```

Tests are intentionally offline-only (no network calls in CI). Backend
integration is exercised via the live `api.gammaqc.com` staging
environment, gated behind `GAMMAQC_BACKEND_URL`.

---

## License

Apache-2.0. The CLI is free as in beer AND free as in speech. The
backend is sovereign-tier proprietary; the API contract is documented
publicly so the CLI can talk to any compatible implementation.

---

## Get a Pro key

[https://gammaqc.com/pro](https://gammaqc.com/pro)

$49/mo unlocks the Council + sealed receipts. $1,995/mo unlocks
institutional rate limits + dedicated compliance attestation chain.
