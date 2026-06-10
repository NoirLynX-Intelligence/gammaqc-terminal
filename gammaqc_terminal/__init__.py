"""gammaqc-terminal — sovereign quantitative-finance CLI.

Free at the surface (raw SEC scrape, Warren-Voice analysis, ASCII trader
cards, local Portfolio Shock Matrix, headless Ghost-Watcher daemon).
PQC-sealed at the wall (10-Seat Council split, cryptographic witness
receipts, autonomous compliance log generation — Pro tier).

Bound to the GammaQC sovereign backend at api.gammaqc.com when
authenticated via `gamma login --api-key`.
"""

__version__ = "0.3.1"
__phase__ = "terminal-v0.3.1"  # v0.3.1 fixes (Commander feedback 2026-06-10):
                               #  - Yahoo v7/quote broken (crumb auth) →
                               #    switched to v8/chart, real prices return
                               #  - First-run welcome / ORA in the terminal
                               #  - `gamma welcome` command for re-display
                               #  - `gamma shock --sample` for demo portfolio
                               #  - Friendly nudge instead of typer error
                               #    when shock args are missing
