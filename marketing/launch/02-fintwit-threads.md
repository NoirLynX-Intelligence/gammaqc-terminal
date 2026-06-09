# Fintwit (X.com) — 5 single-post drops, spread over 2 weeks

> **Brand context:** product is **Gamma QC** (DrkLynX brand). CLI is
> `gammaqc-terminal` / "Gamma QC Terminal". `Gamma` = options trader
> meaning (volatility, curvature, alpha); `QC` = Quantitative
> Cryptography + Quality Control. Institutional-weight name on purpose.
> First link in any post → `install.gammaqc.com`; second → GitHub
> source (proof of the privacy claim).

**Posting cadence:** one every 2-3 days. NOT a thread (threads on
launch posts feel marketery). Each post is standalone, links to a
different feature so the surface area expands organically.

**Common rules:**
- No emoji except in the install snippet (curl/pip lines)
- No `gm` / `gn` openers (reads as crypto-grift)
- No `LFG` / `WAGMI` endings
- Always include the GitHub link (not just the install URL — links
  back to source proves the privacy claim)
- Single screenshot per post (terminal output, Swiss-Brutalist style
  reads well in mobile preview)
- Tag NOTHING. No @-tags. Faceless posture means no engagement-bait.

---

## Post 1 — the hook (portfolio shock-test)

```
Tried to find a free local tool that stress-tests my portfolio against
macro events. Couldn't. Built one.

$ gamma shock --portfolio holdings.csv --event "Fed raises 50bps"

Reads the CSV locally (never leaves your machine), runs a deterministic
Blast Radius Report per ticker. 4 lines of bash to install.

pip install gammaqc-terminal

Source: github.com/NoirLynX-Intelligence/gammaqc-terminal (Apache-2.0)
```

[Attach: screenshot of the shock command output table with NVDA
bleeding -$13,500, JPM gaining $4,800, total -$9,200]

---

## Post 2 — the daemon (Ghost-Watcher)

```
Headless market-condition daemon, runs while you sleep:

$ gamma watch NVDA --trigger "volume_spike > 3.0"

Detaches, polls quote data, fires a native desktop notification +
optional webhook (Discord/Slack) when the trigger hits. No cloud
account, no Zapier, no subscription.

The same shape as a Bloomberg alert minus the $24K/yr.

Code: github.com/NoirLynX-Intelligence/gammaqc-terminal
```

[Attach: screenshot of the watch command running with tick output +
the desktop notification popup that fires when condition hits]

---

## Post 3 — the trader card (visual + Pro upsell)

```
$ gamma analyze NVDA

Pulls last 48hr of SEC filings + quote, prints a Swiss-Brutalist
trader card with a structural read. The 10-Seat sovereign council
split + PQC compliance receipt are Pro-tier ($49/mo) — but the
free card itself is permanently free, no time-bomb.

pip install gammaqc-terminal
```

[Attach: screenshot of the locked Trader Card showing redacted council
seats + the unlock CTA]

---

## Post 4 — the SEC scrape (for the EDGAR nerds)

```
Things I built into gammaqc-terminal because I was tired of writing
them every time:

- $ gamma scrape NVDA → raw JSON to stdout, pipe into jq
- SEC ticker→CIK lookup cached locally
- Last 5 filings with accession numbers ready to grep
- Yahoo quote fields in the same JSON object

The whole scrape stack is in 100 lines of Python. Apache-2.0.
github.com/NoirLynX-Intelligence/gammaqc-terminal
```

[Attach: screenshot of the JSON output from `gamma scrape NVDA | jq`
showing the recent_filings array with Form 4 entries]

---

## Post 5 — the privacy contract (the actual differentiator)

```
Two things I refused to compromise on when building gammaqc-terminal:

1. Your holdings CSV NEVER leaves your machine in free mode. The
   stress-test runs locally. Try it offline.

2. The install script makes ZERO telemetry calls. Verify before piping:
   curl -sL https://install.gammaqc.com/install | less

If you want Pro features (sealed compliance receipts, hedge
generation), you authenticate explicitly. Until then: total radio
silence on your side.

github.com/NoirLynX-Intelligence/gammaqc-terminal
```

[No attachment — text-only post for the privacy people who don't
trust screenshots]

---

## After-launch monitoring

Track these metrics on each post:
- Impressions
- Reposts (signal: genuine interest vs courtesy likes)
- Replies (signal: real users vs bots — manual triage)
- Profile visits → install attempts (proxied by PyPI download bumps in
  the 24h after each post)

If Post 1 underperforms (< 50 reposts in 48h), DON'T post 2 the same
way — pivot the angle. Possible pivots:
- Lead with the Ghost-Watcher daemon instead of shock (different hook)
- Lead with the "$24K Bloomberg saving" angle (more aggressive, higher
  risk of "this is an ad" reflex)
- Lead with a specific use case ("how I stress-tested my crypto book
  during the Friday flash crash")
