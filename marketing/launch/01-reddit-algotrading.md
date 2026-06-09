# r/algotrading — primary launch post

**Subreddit:** r/algotrading
**Suggested title:** `Built a CLI that does portfolio shock-testing locally + reads SEC filings in <1s. Free, Apache-2.0.`
**Flair:** Tools / Show
**Best posting window:** Tuesday or Wednesday, 9-11am ET (when EU + US east are both awake)

---

## Post body

I got tired of either (a) paying for terminal subscriptions or (b)
duct-taping Yahoo Finance into Python notebooks every time I wanted to
stress-test a position. So I built `gammaqc-terminal` — a Python CLI
that does the things I actually want from the command line.

What it does, free tier (no signup, no account):

- `gamma analyze NVDA` — pulls the most recent SEC filings + Yahoo
  quote, prints a structural read of where the position sits
- `gamma shock --portfolio ./holdings.csv --event "Fed raises 50bps"` —
  reads your holdings CSV LOCALLY, runs a deterministic Blast Radius
  Report per ticker. The CSV never leaves your machine. Run it offline.
- `gamma watch NVDA --trigger "volume_spike > 3.0"` — headless daemon
  that fires native desktop notifications + webhooks when conditions
  hit. Same shape as a Bloomberg alert minus the $24K/yr.
- `gamma scrape NVDA` — raw JSON to stdout. Pipe into jq, your bots,
  whatever.

Install:

```bash
pip install gammaqc-terminal
gamma analyze NVDA
```

Or curl-pipe-bash if you prefer:

```bash
curl -sL https://install.gammaqc.com/install | bash
```

(The install script is 30 lines of bash. It runs pipx install if
you have pipx, else pip3 install --user. Zero telemetry. You can
verify before piping: `curl -sL https://install.gammaqc.com/install
| less`.)

**Source:** github.com/NoirLynX-Intelligence/gammaqc-terminal (Apache-2.0)
**PyPI:** pypi.org/project/gammaqc-terminal

### The privacy posture (the reason I built it this way)

- Holdings CSV NEVER leaves your machine in free mode. The `shock`
  command parses it client-side, computes Blast Radius client-side.
  Privacy is the product, not a feature.
- The install script makes ONE network call: the pip install from PyPI.
  No telemetry. No phone-home. No analytics pixel.
- If you authenticate later (Pro tier, see below), the API key is
  stored at your OS-native config dir with 0600 perms. Never logged,
  never echoed.

### The Pro tier wall (transparent — figure I'd rather tell you upfront)

Free tier is what's above. Pro tier ($49/mo) adds:

- Cryptographically-sealed compliance receipts on every Ghost-Watcher
  daemon fire (HMAC-SHA256 today, ML-DSA-65 wire-ready). Useful if
  you're an RIA or anyone who needs an audit trail.
- The Trader Card shows the full 10-Seat sovereign council split
  instead of the redacted version.
- Algorithmic Hedge Strategy: `gamma shock --hedge` calls the backend
  to return per-position hedge recommendations (deterministic rule
  table, not LLM-generated, so the recommendation is reproducible
  for compliance).
- Backend Warren-Voice analysis (this one IS LLM-graded).

Free tier is permanently free. Not a 30-day-trial-then-paywall scheme.

### Why I'm posting this

Honestly, I built it for myself first. Putting it out because the
privacy contract only works at scale — the more eyes on the source,
the more trustworthy "your CSV never leaves your machine" actually is.

Happy to answer technical questions. Feedback on what's missing /
broken is welcome.

---

## What NOT to say if questioned

- Don't claim it replaces Bloomberg — it doesn't. It does a SLICE of
  what Bloomberg does, locally and free. Honest framing.
- Don't promise "AI-powered" features the free tier doesn't actually
  ship.
- Don't argue with people who say "just use yfinance" — they're partly
  right; `scrape` is essentially yfinance with extras. The value is in
  the integrated `shock` + `watch` + signing flow, not the scrape itself.

## Reply templates (for likely top comments)

> "Looks like a thin wrapper around yfinance"

True for `scrape`. The value-add is `shock` (which yfinance doesn't do
and isn't trying to do) + `watch` (which exists but is fragmented) +
the privacy contract (which yfinance can't promise because it's a thin
HTTP client). If you only need raw quotes, stick with yfinance.

> "How is the Blast Radius Report actually calculated?"

It's a heuristic, intentionally. Per-event asset-class beta vectors
mapped to a deterministic rule table — full source in
`gammaqc_terminal/shock.py`. NOT a regression model and NOT trying to
be. The goal is directional ("which positions bleed on Fed +50bps")
not P&L forecast. Honest about its limits.

> "Why should I trust the Pro tier compliance signing?"

The signed payload is HMAC-SHA256 today with a published canonicalization
(sort_keys + ":" separators) so any third party with the verifier key
can validate. The ML-DSA-65 PQC path is wired but not active until the
keypair lands (Phase 2). It's "honest unsigned" until then — clearly
marked, never silently downgraded.

> "Where's the actual code? I want to read it before installing."

github.com/NoirLynX-Intelligence/gammaqc-terminal — start with
gammaqc_terminal/cli.py to see the CLI surface, then shock.py for the
Blast Radius logic, then auth.py to verify the privacy contract.
50 tests covering shock + watch + auth + config; CI matrix runs on
Python 3.10/3.11/3.12 × ubuntu/macos/windows.

> "What if I find a security issue?"

Email ops@gammaqc.com. Or open a GitHub issue with a CVE-style
write-up. Fixed-in-N-days commitment on confirmed reports.
