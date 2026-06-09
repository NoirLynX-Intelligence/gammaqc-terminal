# r/algotrading — primary launch post (Gamma QC Terminal release)

**Subreddit:** r/algotrading
**Title:** `Released a free local CLI for portfolio shock-testing + SEC scrape. Apache-2.0, your CSV stays on your machine.`
**Flair:** Tools / Show
**Best posting window:** Tuesday or Wednesday, 9-11am ET (US-east + EU both awake)

> Product brand: **Gamma QC** (a DrkLynX product). The CLI is "Gamma QC
> Terminal" or `gammaqc-terminal` (PyPI / GitHub). The post stays
> subreddit-appropriate: tool announcement, no brand-forward marketing
> voice. The institutional weight of `Gamma` (volatility / curvature /
> alpha) and `QC` (Quantitative Cryptography + Quality Control) shows
> up when people click through to gammaqc.com — let them discover it.

---

## Post body

I built a Python CLI for the moments when something happens in the
market and I want to know how my book reacts in the next 30 seconds,
without paying for a terminal subscription or refactoring a yfinance
notebook every quarter.

**Install:**

```bash
pip install gammaqc-terminal
```

Or curl-pipe-bash from the install portal:

```bash
curl -sL https://install.gammaqc.com/install | bash
```

The install script is 30 lines of bash. Verify before piping:

```bash
curl -sL https://install.gammaqc.com/install | less
```

**Source:** github.com/NoirLynX-Intelligence/gammaqc-terminal (Apache-2.0)
**PyPI:** pypi.org/project/gammaqc-terminal

---

### Free tier — what it does on your machine

```bash
gamma analyze NVDA
# Pulls last 48hr of SEC filings (EDGAR) + quote + a structural read
# of the position. All from your local box. Renders a Swiss-Brutalist
# trader card.

gamma shock --portfolio ./holdings.csv --event "Fed raises rates 50bps"
# Reads your holdings CSV LOCALLY, computes a deterministic
# Blast Radius Report per ticker. The CSV NEVER leaves your machine.
# Run it offline. (Verify with the network off — it still works.)

gamma watch NVDA --trigger "volume_spike > 3.0"
# Headless daemon. Polls quote data, fires a native desktop
# notification + optional webhook (Discord/Slack) when the trigger
# hits. No cloud account, no Zapier, no subscription.

gamma scrape NVDA
# Raw JSON to stdout. Pipe into jq, your bots, dashboards.
```

### What makes it different from yfinance + Excel

- **Holdings privacy contract.** The CSV never leaves your machine
  in free mode. The `shock` command parses + computes Blast Radius
  client-side. This is the actual value-add — not the scrape layer
  (which is yfinance with extras).
- **Deterministic Blast Radius model.** Per-event asset-class beta
  vectors, mapped to a fixed rule table. Full source in
  `gammaqc_terminal/shock.py`. **Not a regression model and not
  trying to be.** Goal: directional ("which positions bleed on Fed
  +50bps") not P&L forecast. Honest about the limits.
- **Cross-platform.** Python 3.10+ on ubuntu/macos/windows. 50 tests
  in CI covering the 9-platform matrix.

### Pro tier wall (upfront, not hidden)

Free tier above is permanently free. No 30-day-trial-then-paywall.

Pro tier ($49/mo, when you want it) adds:

- 10-Seat Sacred Council split on every trader card
- Cryptographically-sealed compliance receipts on every Ghost-Watcher
  daemon fire (HMAC-SHA256 today, ML-DSA-65 PQC stub ready for the
  keypair rollout). Useful if you're an RIA or anyone who needs a
  reproducible audit trail.
- `gamma shock --hedge` — backend Algorithmic Hedge Strategy. The
  hedge logic is a **deterministic rule table, NOT LLM-generated**.
  Hedge advice has legal exposure; it has to be reproducible from
  inputs for compliance audit. The trade-off vs an LLM is intentional.
- Backend Warren-Voice analysis (this one IS LLM-graded, with per-call
  attestation hash on the response).

---

### Why I'm posting this

The privacy contract ("CSV never leaves your machine") only works at
scale when the source is verifiable. More eyes on the code = more
trust the contract holds. Source is Apache-2.0, on GitHub, with CI
running on every push.

Feedback / breakage reports welcome.

---

## What NOT to claim if questioned

- ❌ Don't say it replaces Bloomberg. It does a slice locally, free.
  Honest framing only.
- ❌ Don't promise the LLM features the free tier doesn't ship.
- ❌ Don't push the Pro tier in the thread. Let people discover it.
- ❌ Don't claim the backend is open. It's not. CLI is Apache-2.0;
  backend is sovereign-tier proprietary. That's the architecture.

## Reply templates for likely top comments

### "Looks like a wrapper around yfinance"

True for `scrape`. The value-add is `shock` (yfinance doesn't do
portfolio stress-tests) + `watch` (yfinance doesn't do headless
daemons with native notifications) + the privacy contract (yfinance
can't promise it because it's a thin HTTP client). If you only need
raw quotes, yfinance is fine — it's a dependency, not a competitor.

### "How is Blast Radius actually calculated?"

Per-event asset-class beta vectors. e.g. `fed_hike` →
`{long_duration_tech: -1.2, growth_tech: -0.9, financials: +0.6, ...}`.
Each ticker maps to an asset class via the CSV's `sector` column
(or a small built-in lookup table for common tickers). Blast Radius =
`position_value × class_beta`. Full source:
[`gammaqc_terminal/shock.py`](https://github.com/NoirLynX-Intelligence/gammaqc-terminal/blob/main/gammaqc_terminal/shock.py).

The betas are conservative and intentionally simple. PRs with
empirically-derived betas from a real factor model are welcome.

### "Why should I trust the Pro tier signing?"

The signed payload is HMAC-SHA256 today with a published
canonicalization (sort_keys + `:` separators, strip `_attestation`
field before re-encoding). Any third party with the verifier key can
validate the signature. The ML-DSA-65 PQC path is wired but stub-
inactive until the keypair lands — explicitly marked as
"honest unsigned" until then, never silently downgraded.

### "Where's the code?"

[github.com/NoirLynX-Intelligence/gammaqc-terminal](https://github.com/NoirLynX-Intelligence/gammaqc-terminal).
Start with `gammaqc_terminal/cli.py` for the CLI surface, then
`shock.py` for the Blast Radius logic, then `auth.py` + `config.py`
to verify the privacy contract.

### "Is the backend open source too?"

The CLI is Apache-2.0. The backend (council + hedge rule table + PQC
attestation infra) is sovereign-tier proprietary — what you pay for at
Pro is access to the canonical backend. The CLI ↔ backend API contract
is documented in the `routes/terminal.py` module on the CLI side, so
the CLI can talk to any compatible implementation if you'd rather
self-host the backend logic.

### "What if I find a security issue?"

ops@gammaqc.com, or a CVE-style GitHub issue. Fixed-in-N-days
commitment on confirmed reports.
