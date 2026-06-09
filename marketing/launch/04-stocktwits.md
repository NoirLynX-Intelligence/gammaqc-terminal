# Stocktwits — plain-language framing for the retail audience

**Audience reality:** Stocktwits skews retail, mobile, lower technical
bar. Posts that read "engineering" get scrolled past. Posts that read
"trader sharing a tool" engage.

**Post cadence:** one post in the general feed, plus 3-5 ticker-specific
posts in $NVDA, $TSLA, $SPY streams (the ones with the most-engaged
retail audiences).

---

## General feed post

```
free tool I built for when fed news drops and you need to know what
in your portfolio gets hit. takes 30 seconds to install, runs locally
(your holdings file never leaves your machine).

curl -sL https://install.gammaqc.com/install | bash
gamma shock --portfolio yourfile.csv --event "Fed raises 50bps"

prints a table. red = bleed, green = gain. that's it.

open source: github.com/NoirLynX-Intelligence/gammaqc-terminal
```

[Attach: same shock screenshot as the Fintwit Post 1]

---

## $NVDA stream post

```
quick way to read NVDA's structural setup from the command line:

gamma analyze NVDA

pulls latest SEC filings (form 4 insider activity, 10-Q diffs etc.)
+ quote data + a 3-bullet structural read. all local. all free.

if you want to see how NVDA reacts in your portfolio when the macro
shifts, gamma shock does that too.

source: github.com/NoirLynX-Intelligence/gammaqc-terminal
```

---

## $TSLA stream post

```
running a TSLA position into the next FOMC and wondering how much
you'll bleed on a 50bps hike?

gamma shock --portfolio holdings.csv --event "Fed raises 50bps"

calculates per-position Blast Radius locally. growth_tech (TSLA's
asset class on the rule table) gets a -0.9 beta on rate-hike events,
so a $10K TSLA position shows roughly -$9K Blast Radius.

it's a heuristic, not a forecast. but it gets the direction right
which is what you need at 2pm on FOMC day.
```

---

## $SPY stream post

```
free CLI for SPY-options people who want a headless alert daemon:

gamma watch SPY --trigger "volume_spike > 2.0"

runs in the background while you work. fires a desktop notification
the moment SPY volume crosses 2x its 3-month average. no Discord bot,
no Zapier, no subscription. just pip install + run.

github.com/NoirLynX-Intelligence/gammaqc-terminal
```

---

## What to AVOID on Stocktwits

- Don't include the "Pro tier $49/mo" wall in these — Stocktwits is
  predominantly free-content seekers; the paywall mention triggers
  scroll-past. Let them discover it on the GitHub link.
- Don't use technical jargon ("HMAC-SHA256", "PQC", "deterministic
  hedge table"). The audience tunes that out.
- Don't bullet-list features. Stocktwits is conversational.
- Don't post all 4 simultaneously — spread over 3-5 days.

## Engagement reply templates

> "is this a pump?"
no, it's a free open-source tool. source code is linked. you don't
have to install it, you can just read the python.

> "what does it actually DO that yfinance doesn't"
yfinance is the scrape layer. gammaqc adds: portfolio stress-test
with deterministic asset-class betas + a watcher daemon + a
compliance receipt chain for the pro tier. if you just want raw
quotes, yfinance is fine.

> "is my portfolio data safe"
the holdings CSV NEVER leaves your machine in free mode. you can
run gamma shock on a plane with the wifi off and it still works.
that's the point.

> "how much does it cost"
free tier is permanently free. pro tier is $49/mo if you want
sealed compliance receipts and the algorithmic hedge generation.
free tier does the shock + watch + scrape + analyze.
