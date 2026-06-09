# gammaqc-terminal launch drafts (faceless drop)

> Honest, technical, non-salesy posts for the install vector's first wave.
> COMMANDER REVIEW REQUIRED BEFORE POSTING — these are drafts, not
> automated outbound.

## Strategy (per Commander's 21-ERT directive)

**Persona:** "I am a retail quant who got tired of paying for Bloomberg
and built a tool." First-person, no marketing voice. The reader should
feel like a peer is sharing a tool, not a vendor pitching.

**Frequency triggered (per Oracle 3-frequency model):**
- **REGRET**: "every month on Bloomberg is $2K you'll never get back"
- **DESIRE**: "options flow + portfolio shock-test on your laptop"
- **FOMO**: not used in launch — saved for retention not acquisition;
  trying to FOMO retail quants reads as ads and triggers reflexive scroll-past

**Truth claims that must hold up:**
1. Apache-2.0 open source (claim → verify at github.com/...)
2. SEC filings + Yahoo quote pulled LOCALLY (claim → verify offline)
3. Holdings CSV never leaves machine in free mode (claim → grep the
   source code for any csv.upload)
4. PQC-sealed Pro receipts (claim → verify the HMAC-SHA256 signature
   chain; ML-DSA-65 stub ready for activation)
5. Free tier is permanently free (claim → no time bomb in install
   script; no PyPI re-rev that flips Pro features behind paywall)

**Distribution surfaces** (in launch order):
1. r/algotrading — most technical, highest BS detector, best signal
2. r/quant — same audience, smaller, more skeptical
3. Hacker News (Show HN) — broader engineering audience; risk of "yet
   another CLI" pile-on, mitigated by the privacy contract angle
4. Fintwit (X.com) — fragmented; needs ~5 posts at different angles
5. Stocktwits — last; lower technical bar; works on plain-language framing

## Anti-patterns to avoid

- ❌ Calling it "AI-powered" (overused; triggers skepticism)
- ❌ Using emoji-heavy launch copy (reads as bot/marketer)
- ❌ Comparing directly to Bloomberg pricing (legal-adjacent + reads as ads)
- ❌ Mentioning the 1500-domain mesh (reads as spam infrastructure)
- ❌ Hiding the Pro paywall (transparency converts; obfuscation kills trust)
- ❌ Posting all 5 platforms same day (looks coordinated; spread over 2 weeks)

## Approval gate

These drafts MUST be reviewed by Commander before posting. The faceless
posture works because the COMMUNICATION is honest. Posting AI-generated
copy uncritically would corrupt that.
