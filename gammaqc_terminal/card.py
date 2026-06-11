"""Trader Card renderer — ASCII / Rich panel format.

Free-tier behavior: renders the directional + structural bullets in a
Swiss-Brutalist black-and-white panel. The 10-Seat Sacred Council split
and the Cryptographic Witness Receipt are visually present but BLURRED
(rendered as `█████` redactions) with a clear unlock callout.

Pro-tier behavior (when /oracle/card/sealed succeeds): the council
split + PQC witness receipt fields are populated from the backend
response and the redactions are replaced with real data + signature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .auth import _client
from .config import Config
from .voice import WarrenAnalysis


@dataclass
class TraderCard:
    ticker: str
    bias: str
    confidence: float
    bullets: list[str]
    council_split: dict[str, str] | None = None    # seat → vote string; None if locked
    witness_receipt: str | None = None              # PQC hash; None if locked
    issued_at: str = ""
    source: str = "local"                           # local | backend
    # v0.3.2: free-tier council always shows 3 unlocked seats
    free_council: list[Any] = None                  # type: ignore  # list[Tuple[str,str,str,int]]


# Seven Pro-tier seats — names visible so free user sees what's behind the wall.
# This is value preview, not vapor: each seat name describes a real institutional
# signal Pro Warren computes via the backend (options flow, factor regression,
# structural break, etc.). Free user sees "the wall isn't decorative — these are
# real seats with real votes when you upgrade".
_PRO_SEAT_NAMES = [
    "Seat 04 · Options Flow",
    "Seat 05 · Factor Regression",
    "Seat 06 · Structural Break",
    "Seat 07 · Earnings Drift",
    "Seat 08 · Insider Cadence",
    "Seat 09 · Cross-Asset Flow",
    "Seat 10 · Q-LAM Synthesis",
]


def build_card_local(ticker: str, warren: WarrenAnalysis) -> TraderCard:
    return TraderCard(
        ticker=ticker.upper(),
        bias=warren.bias,
        confidence=warren.confidence,
        bullets=warren.bullets,
        council_split=None,
        witness_receipt=None,
        issued_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source="local",
        free_council=warren.free_council or [],
    )


def upgrade_card_via_backend(card: TraderCard, cfg: Config) -> TraderCard:
    """Pro lift — calls /oracle/card/sealed, populates council + receipt.
    Falls back to local on any failure (graceful degradation)."""
    if not cfg.api_key:
        return card
    try:
        with _client(cfg, timeout=15.0) as c:
            r = c.post("/api/oracle/card/sealed", json={
                "ticker": card.ticker,
                "bias": card.bias,
                "bullets": card.bullets,
            })
        if r.status_code == 200:
            payload = r.json()
            return TraderCard(
                ticker=card.ticker,
                bias=payload.get("bias", card.bias),
                confidence=float(payload.get("confidence", card.confidence)),
                bullets=payload.get("bullets", card.bullets)[:3],
                council_split=payload.get("council_split"),
                witness_receipt=payload.get("witness_receipt"),
                issued_at=payload.get("issued_at", card.issued_at),
                source="backend",
            )
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return card


def render_card(card: TraderCard, *, locked: bool) -> Panel:
    """Render a TraderCard as a Rich Panel. `locked` controls whether the
    council/receipt rows show redactions + the unlock callout."""
    bias_color = {"long": "bold green", "short": "bold red",
                  "neutral": "bold yellow", "unknown": "dim"}.get(card.bias, "white")

    header = Text()
    header.append(f"  {card.ticker}  ", style="bold white on black")
    header.append(f"  bias: ", style="dim")
    header.append(card.bias.upper(), style=bias_color)
    header.append(f"   confidence: {card.confidence:.0%}", style="dim")

    bullets_block = Text()
    for i, b in enumerate(card.bullets, 1):
        bullets_block.append(f"  {i}. ", style="bold cyan")
        bullets_block.append(b + "\n", style="white")

    council_tbl = Table(show_header=True, header_style="bold magenta", expand=True)
    council_tbl.add_column("Seat", style="dim", width=28)
    council_tbl.add_column("Vote", style="bold", width=10)
    council_tbl.add_column("Why", style="white")
    if card.council_split:
        # Pro/backend response — render all 10 seats with backend data
        for seat, vote in card.council_split.items():
            council_tbl.add_row(seat, vote, "")
    elif locked:
        # FREE TIER: show 3 unlocked seats with their REAL rule-based votes,
        # then list the 7 locked seat NAMES (not anonymous redactions) so the
        # user sees the wall isn't decorative.
        vote_colors = {"BULLISH": "bold green", "BEARISH": "bold red", "NEUTRAL": "bold yellow"}
        for seat_name, vote, why, conv in (card.free_council or []):
            vote_text = Text(f"{vote}", style=vote_colors.get(vote, "white"))
            vote_text.append(f" {conv}/10", style="dim")
            why_text = Text(why, style="dim white")
            council_tbl.add_row(seat_name, vote_text, why_text)
        # 7 locked Pro seats — names visible so free user sees what's coming
        for seat_name in _PRO_SEAT_NAMES:
            council_tbl.add_row(
                Text(seat_name, style="dim"),
                Text("[Pro]", style="dim red"),
                Text("unlocks with --api-key", style="dim italic"),
            )

    receipt_line = Text()
    receipt_line.append("  Witness Receipt: ", style="bold dim")
    if card.witness_receipt:
        receipt_line.append(card.witness_receipt[:64] + "…", style="green")
    elif locked:
        receipt_line.append("[LOCKED — Pro tier unlocks PQC-sealed receipt]", style="red")
    else:
        receipt_line.append("(none)", style="dim")

    footer_lines: list[Text] = [receipt_line]
    if locked:
        unlock = Text()
        unlock.append("\n  ⚠ Regulatory Audit Layer Locked.", style="bold yellow")
        unlock.append("\n  To unlock the 10-Seat Council split and PQC-sealed compliance ", style="dim")
        unlock.append("\n  receipts, authenticate: ", style="dim")
        unlock.append("gamma login --api-key <KEY>", style="bold cyan")
        unlock.append("\n  Get your key at https://gammaqc.com/pro", style="dim")
        footer_lines.append(unlock)

    body = Group(header, Text(), bullets_block, council_tbl, *footer_lines)
    title = "GammaQC TRADER CARD" + (" — locked" if locked else " — sealed")
    return Panel(body, title=title, border_style="white", padding=(1, 2))
